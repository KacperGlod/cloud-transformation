"use strict";

const http = require("http");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");
const { Transform } = require("stream");
const { pipeline } = require("stream/promises");

const ROOT_DIR = __dirname;
const POINTCLOUDS_DIR = path.join(ROOT_DIR, "pointclouds");
const RUNTIME_DIR = path.join(ROOT_DIR, ".runtime");
const UPLOADS_DIR = path.join(RUNTIME_DIR, "uploads");
const JOBS_DIR = path.join(RUNTIME_DIR, "jobs");
const CONVERTER_DIR = path.join(ROOT_DIR, "tools", "PotreeConverter");
const CONVERTER_EXE = path.join(CONVERTER_DIR, "PotreeConverter.exe");
const LASZIP_DLL = path.join(CONVERTER_DIR, "laszip.dll");
const SEGMENTER_SCRIPT = path.join(ROOT_DIR, "tools", "segment_pointcloud.py");
const TRANSFORMER_SCRIPT = path.join(ROOT_DIR, "tools", "transform_pointcloud.py");
const POINT_REMOVER_SCRIPT = path.join(ROOT_DIR, "tools", "remove_points.py");
const PYTHON_COMMAND = process.env.PYTHON || "python";
const HOST = process.env.HOST || "::1";
const PORT = Number(process.env.PORT || 1234);
const MAX_UPLOAD_BYTES = Number(process.env.MAX_UPLOAD_BYTES || 100 * 1024 ** 3);
const MAX_LOG_LENGTH = 60_000;
const POTREE_HIERARCHY_RECORD_BYTES = 22;
const MIN_CONVERSION_RESERVE_BYTES = 1024n ** 3n;

const jobs = new Map();
const conversionQueue = [];
const reservedCloudNames = new Set();
let activeJobId = null;
let activeConverter = null;

const MIME_TYPES = new Map([
	[".css", "text/css; charset=utf-8"],
	[".gif", "image/gif"],
	[".html", "text/html; charset=utf-8"],
	[".ico", "image/x-icon"],
	[".jpeg", "image/jpeg"],
	[".jpg", "image/jpeg"],
	[".js", "application/javascript; charset=utf-8"],
	[".json", "application/json; charset=utf-8"],
	[".map", "application/json; charset=utf-8"],
	[".png", "image/png"],
	[".svg", "image/svg+xml"],
	[".wasm", "application/wasm"],
	[".woff", "font/woff"],
	[".woff2", "font/woff2"],
]);

function sendJson(response, statusCode, payload) {
	const body = Buffer.from(JSON.stringify(payload));
	response.writeHead(statusCode, {
		"Cache-Control": "no-store",
		"Content-Length": body.length,
		"Content-Type": "application/json; charset=utf-8",
		"X-Content-Type-Options": "nosniff",
	});
	response.end(body);
}

function slugify(value) {
	return String(value || "")
		.normalize("NFKD")
		.replace(/[\u0300-\u036f]/g, "")
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "")
		.slice(0, 80);
}

function isPathInside(parent, candidate) {
	const parentPath = path.resolve(parent).toLowerCase();
	const candidatePath = path.resolve(candidate).toLowerCase();
	return candidatePath.startsWith(`${parentPath}${path.sep}`);
}

async function removeRuntimePath(target) {
	if (!isPathInside(RUNTIME_DIR, target)) {
		throw new Error("Odmowa usunięcia pliku spoza katalogu roboczego.");
	}
	await fsp.rm(target, { recursive: true, force: true });
}

async function removeFailedOutput(target) {
	if (!isPathInside(POINTCLOUDS_DIR, target)) {
		throw new Error("Odmowa usunięcia danych spoza katalogu pointclouds.");
	}
	await fsp.rm(target, { recursive: true, force: true });
}

function appendJobLog(job, chunk) {
	const text = chunk.toString("utf8");
	job.log = `${job.log}${text}`.slice(-MAX_LOG_LENGTH);
	job.updatedAt = new Date().toISOString();

	const progressText = `${job.progressParseBuffer || ""}${text}`;
	job.progressParseBuffer = progressText.slice(-2048);

	let pattern;
	if (job.status === "converting") {
		// Example: [44%, 23s], [DISTRIBUTING: 32%, ...][..., CPU: 35%]
		// The first value is the progress of the complete Potree conversion.
		pattern = /(?:^|[\r\n])\s*\[(\d+(?:\.\d+)?)%\s*,/g;
	} else if (job.status === "segmenting") {
		pattern = /(?:^|[\r\n])\s*Segmentacja:\s*(\d+(?:\.\d+)?)%/g;
	} else if (job.status === "transforming") {
		pattern = /(?:^|[\r\n])\s*(?:Analiza|Zapisywanie) transformacji:\s*(\d+(?:\.\d+)?)%/g;
	} else if (job.status === "removing") {
		pattern = /(?:^|[\r\n])\s*Usuwanie punktów:\s*(\d+(?:\.\d+)?)%/g;
	}

	const matches = pattern ? [...progressText.matchAll(pattern)] : [];
	if (matches.length > 0) {
		const value = Number(matches.at(-1)[1]);
		if (Number.isFinite(value)) {
			const normalized = Math.max(0, Math.min(100, value));
			job.progress = Math.max(job.progress || 0, normalized);
		}
	}
}

function publicJob(job) {
	return {
		id: job.id,
		kind: job.kind,
		name: job.name,
		slug: job.slug,
		filename: job.filename,
		status: job.status,
		uploadProgress: job.uploadProgress,
		progress: job.progress,
		pointCloudUrl: job.pointCloudUrl,
		sourceCloud: job.sourceCloud || null,
		mode: job.mode || null,
		selection: job.selection || null,
		transformation: job.transformation || null,
		transformationResult: job.transformationResult || null,
		removalSelections: job.removalSelections || null,
		removalResult: job.removalResult || null,
		error: job.error,
		log: job.log,
		createdAt: job.createdAt,
		updatedAt: job.updatedAt,
	};
}

async function readCloudInfo(directory) {
	const infoPath = path.join(directory, "cloud-info.json");
	try {
		return JSON.parse(await fsp.readFile(infoPath, "utf8"));
	} catch {
		return null;
	}
}

async function findSourceFile(directory, cloudInfo = null) {
	if (cloudInfo && typeof cloudInfo.sourceFile === "string") {
		const declaredPath = path.join(directory, path.basename(cloudInfo.sourceFile));
		if (isPathInside(directory, declaredPath) && fs.existsSync(declaredPath)) {
			return declaredPath;
		}
	}

	const entries = await fsp.readdir(directory, { withFileTypes: true });
	const source = entries.find(entry => entry.isFile() && [".las", ".laz"].includes(path.extname(entry.name).toLowerCase()));
	return source ? path.join(directory, source.name) : null;
}

function formatBytes(value) {
	const bytes = typeof value === "bigint" ? Number(value) : Number(value);
	if (!Number.isFinite(bytes) || bytes < 0) {
		return "nieznany rozmiar";
	}

	const units = ["B", "KB", "MB", "GB", "TB"];
	let amount = bytes;
	let unit = 0;
	while (amount >= 1024 && unit < units.length - 1) {
		amount /= 1024;
		unit++;
	}

	return `${amount.toLocaleString("pl-PL", { maximumFractionDigits: 2 })} ${units[unit]}`;
}

async function readLasStorageEstimate(filePath) {
	const handle = await fsp.open(filePath, "r");
	try {
		const header = Buffer.alloc(375);
		const { bytesRead } = await handle.read(header, 0, header.length, 0);
		if (bytesRead < 227 || header.toString("ascii", 0, 4) !== "LASF") {
			return null;
		}

		const pointRecordLength = header.readUInt16LE(105);
		let pointCount = BigInt(header.readUInt32LE(107));
		const versionMajor = header.readUInt8(24);
		const versionMinor = header.readUInt8(25);
		const headerSize = header.readUInt16LE(94);
		const hasLas14PointCount = versionMajor === 1 && versionMinor >= 4 && headerSize >= 375;
		if (hasLas14PointCount && bytesRead >= 255) {
			const extendedPointCount = header.readBigUInt64LE(247);
			if (extendedPointCount > 0n) {
				pointCount = extendedPointCount;
			}
		}

		if (pointRecordLength === 0 || pointCount === 0n) {
			return null;
		}

		return pointCount * BigInt(pointRecordLength);
	} finally {
		await handle.close();
	}
}

async function ensureConversionDiskSpace(inputPath) {
	const estimatedOctreeBytes = await readLasStorageEstimate(inputPath);
	if (estimatedOctreeBytes === null || typeof fsp.statfs !== "function") {
		return;
	}

	let availableBytes;
	try {
		const disk = await fsp.statfs(POINTCLOUDS_DIR, { bigint: true });
		availableBytes = disk.bavail * disk.bsize;
	} catch {
		return;
	}

	const proportionalReserve = estimatedOctreeBytes / 4n;
	const reserveBytes = proportionalReserve > MIN_CONVERSION_RESERVE_BYTES
		? proportionalReserve
		: MIN_CONVERSION_RESERVE_BYTES;
	const requiredBytes = estimatedOctreeBytes + reserveBytes;

	if (availableBytes < requiredBytes) {
		throw new Error(
			`Za mało wolnego miejsca na konwersję. Dostępne: ${formatBytes(availableBytes)}; `
			+ `szacowane minimum: ${formatBytes(requiredBytes)}. Zwolnij miejsce na dysku i spróbuj ponownie.`
		);
	}
}

async function validatePotree2Output(directory) {
	const metadataPath = path.join(directory, "metadata.json");
	const hierarchyPath = path.join(directory, "hierarchy.bin");
	const octreePath = path.join(directory, "octree.bin");
	const metadata = JSON.parse(await fsp.readFile(metadataPath, "utf8"));

	if (metadata.version !== "2.0") {
		return;
	}

	const [hierarchy, hierarchyStat, octreeStat] = await Promise.all([
		fsp.readFile(hierarchyPath),
		fsp.stat(hierarchyPath, { bigint: true }),
		fsp.stat(octreePath, { bigint: true }),
	]);

	if (hierarchy.length === 0 || hierarchy.length % POTREE_HIERARCHY_RECORD_BYTES !== 0) {
		throw new Error("Plik hierarchy.bin ma nieprawidłowy rozmiar.");
	}

	const firstChunkSize = Number(metadata.hierarchy?.firstChunkSize);
	if (!Number.isInteger(firstChunkSize) || firstChunkSize <= 0 || firstChunkSize > hierarchy.length) {
		throw new Error("Metadane wskazują nieprawidłowy pierwszy fragment hierarchii.");
	}

	let requiredOctreeBytes = 0n;
	let requiredHierarchyBytes = BigInt(firstChunkSize);
	for (let offset = 0; offset < hierarchy.length; offset += POTREE_HIERARCHY_RECORD_BYTES) {
		const nodeType = hierarchy.readUInt8(offset);
		const byteOffset = hierarchy.readBigInt64LE(offset + 6);
		const byteSize = hierarchy.readBigInt64LE(offset + 14);
		if (byteOffset < 0n || byteSize < 0n) {
			throw new Error("Hierarchia Potree zawiera ujemny zakres danych.");
		}

		const end = byteOffset + byteSize;
		if (nodeType === 2) {
			requiredHierarchyBytes = end > requiredHierarchyBytes ? end : requiredHierarchyBytes;
		} else {
			requiredOctreeBytes = end > requiredOctreeBytes ? end : requiredOctreeBytes;
		}
	}

	if (requiredHierarchyBytes > hierarchyStat.size) {
		const missingBytes = requiredHierarchyBytes - hierarchyStat.size;
		throw new Error(`Plik hierarchy.bin jest niekompletny. Brakuje ${formatBytes(missingBytes)}.`);
	}
	if (requiredOctreeBytes > octreeStat.size) {
		const missingBytes = requiredOctreeBytes - octreeStat.size;
		throw new Error(
			`Plik octree.bin jest niekompletny. Brakuje ${formatBytes(missingBytes)}. `
			+ "Najczęstszą przyczyną jest brak wolnego miejsca podczas konwersji."
		);
	}
}

async function listPointClouds() {
	await fsp.mkdir(POINTCLOUDS_DIR, { recursive: true });
	const entries = await fsp.readdir(POINTCLOUDS_DIR, { withFileTypes: true });
	const clouds = [];

	for (const entry of entries) {
		if (!entry.isDirectory()) {
			continue;
		}

		const directory = path.join(POINTCLOUDS_DIR, entry.name);
		const metadataPath = path.join(directory, "metadata.json");
		const legacyPath = path.join(directory, "cloud.js");
		let entryFile = null;

		if (fs.existsSync(metadataPath)) {
			entryFile = "metadata.json";
		} else if (fs.existsSync(legacyPath)) {
			entryFile = "cloud.js";
		}

		if (entryFile) {
			const cloudInfo = await readCloudInfo(directory);
			const sourceFile = await findSourceFile(directory, cloudInfo);
			let validationError = null;
			if (entryFile === "metadata.json") {
				try {
					await validatePotree2Output(directory);
				} catch (error) {
					validationError = error.message;
				}
			}
			clouds.push({
				name: cloudInfo?.name || entry.name,
				slug: entry.name,
				url: `/pointclouds/${encodeURIComponent(entry.name)}/${entryFile}`,
				format: entryFile === "metadata.json" ? "Potree 2.x" : "Potree 1.x",
				sourceAvailable: Boolean(sourceFile),
				sourceFile: sourceFile ? path.basename(sourceFile) : null,
				parentCloud: cloudInfo?.parentCloud || null,
				operation: cloudInfo?.operation || null,
				valid: validationError === null,
				validationError,
			});
		}
	}

	return clouds.sort((left, right) => left.name.localeCompare(right.name, "pl"));
}

async function runSegmentation(job) {
	job.status = "segmenting";
	job.progress = 0;
	job.updatedAt = new Date().toISOString();
	let completeOutput = "";

	await new Promise((resolve, reject) => {
		const segmenter = spawn(
			PYTHON_COMMAND,
			[
				SEGMENTER_SCRIPT,
				"--input", job.sourcePath,
				"--output", job.inputPath,
				"--laszip", LASZIP_DLL,
				"--matrix", JSON.stringify(job.sourceToBoxMatrix),
				"--mode", job.mode,
			],
			{
				cwd: ROOT_DIR,
				shell: false,
				windowsHide: true,
			}
		);

		activeConverter = segmenter;
		const onOutput = chunk => {
			appendJobLog(job, chunk);
			completeOutput = `${completeOutput}${chunk.toString("utf8")}`.slice(-200_000);
		};

		segmenter.stdout.on("data", onOutput);
		segmenter.stderr.on("data", onOutput);
		segmenter.once("error", error => {
			activeConverter = null;
			if (error.code === "ENOENT") {
				reject(new Error(`Nie znaleziono interpretera Python (${PYTHON_COMMAND}).`));
			} else {
				reject(error);
			}
		});
		segmenter.once("close", code => {
			activeConverter = null;
			if (code === 0) {
				resolve();
			} else {
				reject(new Error(`Segmentacja zakończyła pracę z kodem ${code}. Sprawdź szczegóły zadania.`));
			}
		});
	});

	const resultLines = [...completeOutput.matchAll(/^SEGMENT_RESULT\s+(.+)$/gm)];
	if (resultLines.length === 0) {
		throw new Error("Segmentacja nie zwróciła informacji o pliku wynikowym.");
	}

	try {
		job.selection = JSON.parse(resultLines.at(-1)[1]);
	} catch {
		throw new Error("Segmentacja zwróciła nieprawidłowe podsumowanie.");
	}

	if (!fs.existsSync(job.inputPath)) {
		throw new Error("Segmentacja nie utworzyła wynikowego pliku LAZ.");
	}
}

async function runTransformation(job) {
	job.status = "transforming";
	job.progress = 0;
	job.updatedAt = new Date().toISOString();
	let completeOutput = "";

	await new Promise((resolve, reject) => {
		const transformer = spawn(
			PYTHON_COMMAND,
			[
				TRANSFORMER_SCRIPT,
				"--input", job.sourcePath,
				"--output", job.inputPath,
				"--laszip", LASZIP_DLL,
				"--translation", JSON.stringify(job.transformation.translation),
				"--rotation", JSON.stringify(job.transformation.rotationDegrees),
				"--scale", JSON.stringify(job.transformation.scale),
				"--pivot-mode", job.transformation.pivotMode,
				"--pivot", JSON.stringify(job.transformation.pivot),
			],
			{
				cwd: ROOT_DIR,
				shell: false,
				windowsHide: true,
			}
		);

		activeConverter = transformer;
		const onOutput = chunk => {
			appendJobLog(job, chunk);
			completeOutput = `${completeOutput}${chunk.toString("utf8")}`.slice(-200_000);
		};

		transformer.stdout.on("data", onOutput);
		transformer.stderr.on("data", onOutput);
		transformer.once("error", error => {
			activeConverter = null;
			if (error.code === "ENOENT") {
				reject(new Error(`Nie znaleziono interpretera Python (${PYTHON_COMMAND}).`));
			} else {
				reject(error);
			}
		});
		transformer.once("close", code => {
			activeConverter = null;
			if (code === 0) {
				resolve();
			} else {
				reject(new Error(`Transformacja zakończyła pracę z kodem ${code}. Sprawdź szczegóły zadania.`));
			}
		});
	});

	const resultLines = [...completeOutput.matchAll(/^TRANSFORM_RESULT\s+(.+)$/gm)];
	if (resultLines.length === 0) {
		throw new Error("Transformacja nie zwróciła informacji o pliku wynikowym.");
	}

	try {
		job.transformationResult = JSON.parse(resultLines.at(-1)[1]);
	} catch {
		throw new Error("Transformacja zwróciła nieprawidłowe podsumowanie.");
	}

	if (!fs.existsSync(job.inputPath)) {
		throw new Error("Transformacja nie utworzyła wynikowego pliku LAZ.");
	}
}

async function runPointRemoval(job) {
	job.status = "removing";
	job.progress = 0;
	job.updatedAt = new Date().toISOString();
	let completeOutput = "";

	await new Promise((resolve, reject) => {
		const remover = spawn(
			PYTHON_COMMAND,
			[
				POINT_REMOVER_SCRIPT,
				"--input", job.sourcePath,
				"--output", job.inputPath,
				"--laszip", LASZIP_DLL,
				"--selections", JSON.stringify(job.removalSelections),
			],
			{
				cwd: ROOT_DIR,
				shell: false,
				windowsHide: true,
			}
		);

		activeConverter = remover;
		const onOutput = chunk => {
			appendJobLog(job, chunk);
			completeOutput = `${completeOutput}${chunk.toString("utf8")}`.slice(-200_000);
		};

		remover.stdout.on("data", onOutput);
		remover.stderr.on("data", onOutput);
		remover.once("error", error => {
			activeConverter = null;
			if (error.code === "ENOENT") {
				reject(new Error(`Nie znaleziono interpretera Python (${PYTHON_COMMAND}).`));
			} else {
				reject(error);
			}
		});
		remover.once("close", code => {
			activeConverter = null;
			if (code === 0) {
				resolve();
			} else {
				const errorLines = [...completeOutput.matchAll(/^BŁĄD USUWANIA PUNKTÓW:\s*(.+)$/gm)];
				const message = errorLines.at(-1)?.[1]
					|| `Usuwanie punktów zakończyło pracę z kodem ${code}. Sprawdź szczegóły zadania.`;
				reject(new Error(message));
			}
		});
	});

	const resultLines = [...completeOutput.matchAll(/^POINT_REMOVAL_RESULT\s+(.+)$/gm)];
	if (resultLines.length === 0) {
		throw new Error("Usuwanie punktów nie zwróciło informacji o pliku wynikowym.");
	}

	try {
		job.removalResult = JSON.parse(resultLines.at(-1)[1]);
	} catch {
		throw new Error("Usuwanie punktów zwróciło nieprawidłowe podsumowanie.");
	}

	if (!fs.existsSync(job.inputPath)) {
		throw new Error("Usuwanie punktów nie utworzyło wynikowego pliku LAZ.");
	}
}

async function writeCloudInfo(job) {
	const info = {
		schemaVersion: 1,
		id: job.slug,
		name: job.name,
		slug: job.slug,
		sourceFile: job.filename,
		pointCloudFile: "metadata.json",
		createdAt: new Date().toISOString(),
		parentCloud: job.sourceCloud || null,
		operation: job.kind === "segmentation"
			? {
				type: "segmentation",
				mode: job.mode,
				clipBox: job.clipBox,
				sourceToBoxMatrix: job.sourceToBoxMatrix,
				statistics: job.selection,
			}
			: job.kind === "transformation"
				? {
					type: "transformation",
					...job.transformation,
					result: job.transformationResult,
				}
				: job.kind === "point-removal"
					? {
						type: "point-removal",
						selectionShape: "sphere",
						selections: job.removalSelections,
						statistics: job.removalResult,
					}
					: {
						type: "import",
						originalFilename: job.filename,
					},
	};

	await fsp.writeFile(
		path.join(job.outputDir, "cloud-info.json"),
		`${JSON.stringify(info, null, 2)}\n`,
		{ encoding: "utf8", flag: "wx" }
	);
}

async function startNextConversion() {
	if (activeJobId || conversionQueue.length === 0) {
		return;
	}

	const jobId = conversionQueue.shift();
	const job = jobs.get(jobId);
	if (!job || job.status !== "queued") {
		setImmediate(startNextConversion);
		return;
	}

	activeJobId = job.id;
	job.status = "converting";
	job.updatedAt = new Date().toISOString();
	let outputCreated = false;

	try {
		if (job.kind === "segmentation") {
			await runSegmentation(job);
			job.progress = 0;
		} else if (job.kind === "transformation") {
			await runTransformation(job);
			job.progress = 0;
		} else if (job.kind === "point-removal") {
			await runPointRemoval(job);
			job.progress = 0;
		}

		job.status = "converting";
		job.updatedAt = new Date().toISOString();
		await ensureConversionDiskSpace(job.inputPath);
		await fsp.mkdir(job.outputDir, { recursive: false });
		outputCreated = true;

		await new Promise((resolve, reject) => {
			const logPath = path.join(job.outputDir, "conversion.log");
			const logStream = fs.createWriteStream(logPath, { flags: "wx" });
			const converter = spawn(
				CONVERTER_EXE,
				[job.inputPath, "-o", job.outputDir, "-m", "poisson"],
				{
					cwd: CONVERTER_DIR,
					shell: false,
					windowsHide: true,
				}
			);

			activeConverter = converter;
			logStream.write([
				`Cloud: ${job.name}`,
				`Source file: ${job.filename}`,
				`Operation: ${job.kind}`,
				...(job.sourceCloud ? [`Parent cloud: ${job.sourceCloud}`] : []),
				...(job.kind === "segmentation" ? [`Selection: ${job.mode}`] : []),
				...(job.kind === "transformation" ? [
					`Translation: ${JSON.stringify(job.transformation.translation)}`,
					`Rotation XYZ [deg]: ${JSON.stringify(job.transformation.rotationDegrees)}`,
					`Scale XYZ: ${JSON.stringify(job.transformation.scale)}`,
					`Pivot: ${job.transformation.pivotMode} ${JSON.stringify(job.transformation.pivot)}`,
				] : []),
				...(job.kind === "point-removal" ? [
					`Spherical selections: ${job.removalSelections.length}`,
				] : []),
				`Started: ${new Date().toISOString()}`,
				`Command: PotreeConverter.exe <source> -o <output> -m poisson`,
				"",
			].join("\n"));

			const onOutput = chunk => {
				appendJobLog(job, chunk);
				logStream.write(chunk);
			};

			converter.stdout.on("data", onOutput);
			converter.stderr.on("data", onOutput);
			converter.once("error", error => {
				logStream.destroy();
				reject(error);
			});
			logStream.once("error", error => {
				converter.kill();
				reject(error);
			});
			converter.once("close", code => {
				activeConverter = null;
				logStream.end(`\nFinished: ${new Date().toISOString()}\nExit code: ${code}\n`);
				logStream.once("finish", () => {
					if (code === 0) {
						resolve();
					} else {
						reject(new Error(`PotreeConverter zakończył pracę z kodem ${code}.`));
					}
				});
			});
		});

		const metadataPath = path.join(job.outputDir, "metadata.json");
		if (!fs.existsSync(metadataPath)) {
			throw new Error("Konwerter nie utworzył pliku metadata.json.");
		}

		await validatePotree2Output(job.outputDir);

		const originalFilePath = path.join(job.outputDir, job.filename);
		if (!isPathInside(job.outputDir, originalFilePath)) {
			throw new Error("Nieprawidłowa nazwa oryginalnego pliku.");
		}
		await fsp.rename(job.inputPath, originalFilePath);
		await writeCloudInfo(job);
		if (job.kind === "segmentation") {
			await fsp.writeFile(
				path.join(job.outputDir, "segmentation.log"),
				`${job.log}\n`,
				"utf8"
			);
		} else if (job.kind === "transformation") {
			await fsp.writeFile(
				path.join(job.outputDir, "transformation.log"),
				`${job.log}\n`,
				"utf8"
			);
		} else if (job.kind === "point-removal") {
			await fsp.writeFile(
				path.join(job.outputDir, "point-removal.log"),
				`${job.log}\n`,
				"utf8"
			);
		}

		job.status = "completed";
		job.progress = 100;
		job.pointCloudUrl = `/pointclouds/${encodeURIComponent(job.slug)}/metadata.json`;
		job.updatedAt = new Date().toISOString();
	} catch (error) {
		job.status = "failed";
		job.error = error.message;
		job.updatedAt = new Date().toISOString();
		reservedCloudNames.delete(job.slug);
		if (outputCreated) {
			await removeFailedOutput(job.outputDir).catch(() => {});
		}
	} finally {
		await removeRuntimePath(job.workDir).catch(() => {});
		activeJobId = null;
		activeConverter = null;
		setImmediate(startNextConversion);
	}
}

async function handleUpload(request, response, url) {
	if (!fs.existsSync(CONVERTER_EXE)) {
		sendJson(response, 503, { error: "Nie znaleziono tools/PotreeConverter/PotreeConverter.exe." });
		return;
	}

	const filename = path.basename(url.searchParams.get("filename") || "");
	const extension = path.extname(filename).toLowerCase();
	if (!filename || ![".las", ".laz"].includes(extension)) {
		sendJson(response, 400, { error: "Dozwolone są wyłącznie pliki LAS i LAZ." });
		return;
	}

	const requestedName = String(url.searchParams.get("name") || path.parse(filename).name).trim();
	const slug = slugify(requestedName);
	if (!slug) {
		sendJson(response, 400, { error: "Podaj prawidłową nazwę chmury." });
		return;
	}

	const outputDir = path.join(POINTCLOUDS_DIR, slug);
	if (!isPathInside(POINTCLOUDS_DIR, outputDir)) {
		sendJson(response, 400, { error: "Nieprawidłowa nazwa chmury." });
		return;
	}

	if (reservedCloudNames.has(slug) || fs.existsSync(outputDir)) {
		sendJson(response, 409, { error: `Chmura o nazwie "${slug}" już istnieje.` });
		return;
	}

	const declaredLength = Number(request.headers["content-length"] || 0);
	if (declaredLength > MAX_UPLOAD_BYTES) {
		sendJson(response, 413, { error: "Plik przekracza dopuszczalny rozmiar uploadu." });
		return;
	}

	const id = crypto.randomUUID();
	const uploadDir = path.join(UPLOADS_DIR, id);
	const inputPath = path.join(uploadDir, `source${extension}`);
	const now = new Date().toISOString();
	const job = {
		id,
		kind: "import",
		name: requestedName.slice(0, 120),
		slug,
		filename,
		status: "uploading",
		uploadProgress: 0,
		progress: 0,
		pointCloudUrl: null,
		error: null,
		log: "",
		createdAt: now,
		updatedAt: now,
		workDir: uploadDir,
		inputPath,
		outputDir,
	};

	jobs.set(id, job);
	reservedCloudNames.add(slug);
	await fsp.mkdir(uploadDir, { recursive: true });

	let receivedBytes = 0;
	const meter = new Transform({
		transform(chunk, encoding, callback) {
			receivedBytes += chunk.length;
			if (receivedBytes > MAX_UPLOAD_BYTES) {
				callback(new Error("Plik przekracza dopuszczalny rozmiar uploadu."));
				return;
			}

			job.uploadProgress = declaredLength > 0
				? Math.min(100, Math.round((receivedBytes / declaredLength) * 100))
				: null;
			job.updatedAt = new Date().toISOString();
			callback(null, chunk);
		},
	});

	try {
		await pipeline(request, meter, fs.createWriteStream(inputPath, { flags: "wx" }));
		if (receivedBytes === 0) {
			throw new Error("Przesłany plik jest pusty.");
		}

		job.uploadProgress = 100;
		job.status = "queued";
		job.updatedAt = new Date().toISOString();
		conversionQueue.push(job.id);
		sendJson(response, 202, publicJob(job));
		setImmediate(startNextConversion);
	} catch (error) {
		job.status = "failed";
		job.error = error.message;
		job.updatedAt = new Date().toISOString();
		reservedCloudNames.delete(slug);
		await removeRuntimePath(uploadDir).catch(() => {});
		if (!response.headersSent) {
			sendJson(response, 400, publicJob(job));
		}
	}
}

async function readJsonBody(request, maxBytes = 128 * 1024) {
	const chunks = [];
	let receivedBytes = 0;
	for await (const chunk of request) {
		receivedBytes += chunk.length;
		if (receivedBytes > maxBytes) {
			throw new Error("Żądanie jest zbyt duże.");
		}
		chunks.push(chunk);
	}

	if (receivedBytes === 0) {
		throw new Error("Brak danych żądania.");
	}

	try {
		return JSON.parse(Buffer.concat(chunks).toString("utf8"));
	} catch {
		throw new Error("Nieprawidłowy format danych JSON.");
	}
}

function normalizedMatrix(value, label) {
	if (!Array.isArray(value) || value.length !== 16) {
		throw new Error(`${label} musi zawierać 16 wartości.`);
	}

	const matrix = value.map(Number);
	if (!matrix.every(Number.isFinite)) {
		throw new Error(`${label} zawiera nieprawidłowe wartości.`);
	}
	return matrix;
}

function normalizedVector(value, length, label) {
	if (!Array.isArray(value) || value.length !== length) {
		throw new Error(`${label} musi zawierać ${length} wartości.`);
	}
	const vector = value.map(Number);
	if (!vector.every(Number.isFinite)) {
		throw new Error(`${label} zawiera nieprawidłowe wartości.`);
	}
	return vector;
}

function normalizedRemovalSelections(value) {
	if (!Array.isArray(value) || value.length === 0) {
		throw new Error("Dodaj co najmniej jedno zaznaczenie punktów.");
	}
	if (value.length > 500) {
		throw new Error("Jedna operacja może zawierać najwyżej 500 zaznaczeń.");
	}

	return value.map((selection, index) => {
		if (!selection || typeof selection !== "object") {
			throw new Error(`Zaznaczenie ${index + 1} ma nieprawidłowy format.`);
		}
		const position = normalizedVector(selection.position, 3, `Pozycja zaznaczenia ${index + 1}`);
		const radius = Number(selection.radius);
		if (!Number.isFinite(radius) || radius <= 0 || radius > 1_000_000_000) {
			throw new Error(`Promień zaznaczenia ${index + 1} musi być większy od zera.`);
		}
		return { position, radius };
	});
}

async function handleSegmentation(request, response) {
	if (!fs.existsSync(CONVERTER_EXE) || !fs.existsSync(LASZIP_DLL) || !fs.existsSync(SEGMENTER_SCRIPT)) {
		sendJson(response, 503, { error: "Lokalny silnik segmentacji nie jest kompletny." });
		return;
	}

	let body;
	try {
		body = await readJsonBody(request);
	} catch (error) {
		sendJson(response, 400, { error: error.message });
		return;
	}

	try {
		const requestedName = String(body.name || "").trim();
		const slug = slugify(requestedName);
		if (!slug) {
			throw new Error("Podaj prawidłową nazwę chmury wynikowej.");
		}

		const mode = String(body.mode || "");
		if (!["inside", "outside"].includes(mode)) {
			throw new Error("Wybierz zachowanie punktów wewnątrz albo na zewnątrz clipping boxa.");
		}

		const sourceCloud = String(body.sourceCloud || "");
		if (!sourceCloud || path.basename(sourceCloud) !== sourceCloud || [".", ".."].includes(sourceCloud)) {
			throw new Error("Nieprawidłowa chmura źródłowa.");
		}

		const sourceToBoxMatrix = normalizedMatrix(body.sourceToBoxMatrix, "Macierz źródło-clipping box");
		const clipBoxData = body.clipBox || {};
		const clipBox = {
			name: String(clipBoxData.name || "Clipping box").slice(0, 120),
			matrixWorld: normalizedMatrix(clipBoxData.matrixWorld, "Macierz clipping boxa"),
			position: normalizedVector(clipBoxData.position, 3, "Pozycja clipping boxa"),
			rotation: normalizedVector(clipBoxData.rotation, 3, "Obrót clipping boxa"),
			scale: normalizedVector(clipBoxData.scale, 3, "Skala clipping boxa"),
			pointCloudMatrixWorld: normalizedMatrix(clipBoxData.pointCloudMatrixWorld, "Macierz chmury źródłowej"),
			pointCloudOffset: normalizedVector(clipBoxData.pointCloudOffset, 3, "Offset chmury źródłowej"),
		};

		const outputDir = path.join(POINTCLOUDS_DIR, slug);
		if (!isPathInside(POINTCLOUDS_DIR, outputDir)) {
			throw new Error("Nieprawidłowa nazwa chmury wynikowej.");
		}
		if (reservedCloudNames.has(slug) || fs.existsSync(outputDir)) {
			sendJson(response, 409, { error: `Chmura o nazwie "${slug}" już istnieje.` });
			return;
		}

		const sourceDir = path.join(POINTCLOUDS_DIR, sourceCloud);
		if (!isPathInside(POINTCLOUDS_DIR, sourceDir)) {
			throw new Error("Nieprawidłowa chmura źródłowa.");
		}
		const sourceStat = await fsp.stat(sourceDir).catch(() => null);
		if (!sourceStat?.isDirectory()) {
			throw new Error("Nie znaleziono katalogu chmury źródłowej.");
		}

		const sourceInfo = await readCloudInfo(sourceDir);
		const sourcePath = await findSourceFile(sourceDir, sourceInfo);
		if (!sourcePath) {
			throw new Error("Chmura źródłowa nie ma zachowanego oryginalnego pliku LAS/LAZ.");
		}

		const id = crypto.randomUUID();
		const workDir = path.join(JOBS_DIR, id);
		const filename = `${slug}.laz`;
		const inputPath = path.join(workDir, filename);
		const now = new Date().toISOString();
		const job = {
			id,
			kind: "segmentation",
			name: requestedName.slice(0, 120),
			slug,
			filename,
			status: "queued",
			uploadProgress: null,
			progress: 0,
			pointCloudUrl: null,
			error: null,
			log: "",
			createdAt: now,
			updatedAt: now,
			workDir,
			inputPath,
			outputDir,
			sourceCloud,
			sourcePath,
			mode,
			clipBox,
			sourceToBoxMatrix,
			selection: null,
		};

		await fsp.mkdir(workDir, { recursive: true });
		jobs.set(id, job);
		reservedCloudNames.add(slug);
		conversionQueue.push(id);
		sendJson(response, 202, publicJob(job));
		setImmediate(startNextConversion);
	} catch (error) {
		sendJson(response, 400, { error: error.message });
	}
}

async function handleTransformation(request, response) {
	if (!fs.existsSync(CONVERTER_EXE) || !fs.existsSync(LASZIP_DLL) || !fs.existsSync(TRANSFORMER_SCRIPT)) {
		sendJson(response, 503, { error: "Lokalny silnik transformacji nie jest kompletny." });
		return;
	}

	let body;
	try {
		body = await readJsonBody(request);
	} catch (error) {
		sendJson(response, 400, { error: error.message });
		return;
	}

	try {
		const requestedName = String(body.name || "").trim();
		const slug = slugify(requestedName);
		if (!slug) {
			throw new Error("Podaj prawidłową nazwę chmury wynikowej.");
		}

		const sourceCloud = String(body.sourceCloud || "");
		if (!sourceCloud || path.basename(sourceCloud) !== sourceCloud || [".", ".."].includes(sourceCloud)) {
			throw new Error("Nieprawidłowa chmura źródłowa.");
		}

		const translation = normalizedVector(body.translation, 3, "Przesunięcie");
		const rotationDegrees = normalizedVector(body.rotationDegrees, 3, "Obrót");
		const scale = normalizedVector(body.scale, 3, "Skala");
		if (scale.some(value => value <= 0 || value > 1_000_000_000)) {
			throw new Error("Wartości skali muszą być większe od zera i nie większe niż 1 000 000 000.");
		}

		const pivotMode = String(body.pivotMode || "center");
		if (!["center", "origin", "custom"].includes(pivotMode)) {
			throw new Error("Wybierz prawidłowy punkt odniesienia transformacji.");
		}
		const pivot = normalizedVector(body.pivot || [0, 0, 0], 3, "Punkt obrotu");

		const outputDir = path.join(POINTCLOUDS_DIR, slug);
		if (!isPathInside(POINTCLOUDS_DIR, outputDir)) {
			throw new Error("Nieprawidłowa nazwa chmury wynikowej.");
		}
		if (reservedCloudNames.has(slug) || fs.existsSync(outputDir)) {
			sendJson(response, 409, { error: `Chmura o nazwie "${slug}" już istnieje.` });
			return;
		}

		const sourceDir = path.join(POINTCLOUDS_DIR, sourceCloud);
		if (!isPathInside(POINTCLOUDS_DIR, sourceDir)) {
			throw new Error("Nieprawidłowa chmura źródłowa.");
		}
		const sourceStat = await fsp.stat(sourceDir).catch(() => null);
		if (!sourceStat?.isDirectory()) {
			throw new Error("Nie znaleziono katalogu chmury źródłowej.");
		}

		const sourceInfo = await readCloudInfo(sourceDir);
		const sourcePath = await findSourceFile(sourceDir, sourceInfo);
		if (!sourcePath) {
			throw new Error("Chmura źródłowa nie ma zachowanego oryginalnego pliku LAS/LAZ.");
		}

		const id = crypto.randomUUID();
		const workDir = path.join(JOBS_DIR, id);
		const filename = `${slug}.laz`;
		const inputPath = path.join(workDir, filename);
		const now = new Date().toISOString();
		const job = {
			id,
			kind: "transformation",
			name: requestedName.slice(0, 120),
			slug,
			filename,
			status: "queued",
			uploadProgress: null,
			progress: 0,
			pointCloudUrl: null,
			error: null,
			log: "",
			createdAt: now,
			updatedAt: now,
			workDir,
			inputPath,
			outputDir,
			sourceCloud,
			sourcePath,
			transformation: {
				translation,
				rotationDegrees,
				rotationOrder: "XYZ",
				scale,
				pivotMode,
				pivot,
			},
			transformationResult: null,
		};

		await fsp.mkdir(workDir, { recursive: true });
		jobs.set(id, job);
		reservedCloudNames.add(slug);
		conversionQueue.push(id);
		sendJson(response, 202, publicJob(job));
		setImmediate(startNextConversion);
	} catch (error) {
		sendJson(response, 400, { error: error.message });
	}
}

async function handlePointRemoval(request, response) {
	if (!fs.existsSync(CONVERTER_EXE) || !fs.existsSync(LASZIP_DLL) || !fs.existsSync(POINT_REMOVER_SCRIPT)) {
		sendJson(response, 503, { error: "Lokalny silnik usuwania punktów nie jest kompletny." });
		return;
	}

	let body;
	try {
		body = await readJsonBody(request);
	} catch (error) {
		sendJson(response, 400, { error: error.message });
		return;
	}

	try {
		const requestedName = String(body.name || "").trim();
		const slug = slugify(requestedName);
		if (!slug) {
			throw new Error("Podaj prawidłową nazwę chmury wynikowej.");
		}

		const sourceCloud = String(body.sourceCloud || "");
		if (!sourceCloud || path.basename(sourceCloud) !== sourceCloud || [".", ".."].includes(sourceCloud)) {
			throw new Error("Nieprawidłowa chmura źródłowa.");
		}
		const removalSelections = normalizedRemovalSelections(body.selections);

		const outputDir = path.join(POINTCLOUDS_DIR, slug);
		if (!isPathInside(POINTCLOUDS_DIR, outputDir)) {
			throw new Error("Nieprawidłowa nazwa chmury wynikowej.");
		}
		if (reservedCloudNames.has(slug) || fs.existsSync(outputDir)) {
			sendJson(response, 409, { error: `Chmura o nazwie "${slug}" już istnieje.` });
			return;
		}

		const sourceDir = path.join(POINTCLOUDS_DIR, sourceCloud);
		if (!isPathInside(POINTCLOUDS_DIR, sourceDir)) {
			throw new Error("Nieprawidłowa chmura źródłowa.");
		}
		const sourceStat = await fsp.stat(sourceDir).catch(() => null);
		if (!sourceStat?.isDirectory()) {
			throw new Error("Nie znaleziono katalogu chmury źródłowej.");
		}

		const sourceInfo = await readCloudInfo(sourceDir);
		const sourcePath = await findSourceFile(sourceDir, sourceInfo);
		if (!sourcePath) {
			throw new Error("Chmura źródłowa nie ma zachowanego oryginalnego pliku LAS/LAZ.");
		}

		const id = crypto.randomUUID();
		const workDir = path.join(JOBS_DIR, id);
		const filename = `${slug}.laz`;
		const inputPath = path.join(workDir, filename);
		const now = new Date().toISOString();
		const job = {
			id,
			kind: "point-removal",
			name: requestedName.slice(0, 120),
			slug,
			filename,
			status: "queued",
			uploadProgress: null,
			progress: 0,
			pointCloudUrl: null,
			error: null,
			log: "",
			createdAt: now,
			updatedAt: now,
			workDir,
			inputPath,
			outputDir,
			sourceCloud,
			sourcePath,
			removalSelections,
			removalResult: null,
		};

		await fsp.mkdir(workDir, { recursive: true });
		jobs.set(id, job);
		reservedCloudNames.add(slug);
		conversionQueue.push(id);
		sendJson(response, 202, publicJob(job));
		setImmediate(startNextConversion);
	} catch (error) {
		sendJson(response, 400, { error: error.message });
	}
}

async function handleApi(request, response, url) {
	if (request.method === "GET" && url.pathname === "/api/health") {
		const clouds = await listPointClouds();
		sendJson(response, 200, {
			ok: true,
			converterAvailable: fs.existsSync(CONVERTER_EXE),
			segmentationAvailable: fs.existsSync(LASZIP_DLL) && fs.existsSync(SEGMENTER_SCRIPT),
			transformationAvailable: fs.existsSync(LASZIP_DLL) && fs.existsSync(TRANSFORMER_SCRIPT),
			pointRemovalAvailable: fs.existsSync(LASZIP_DLL) && fs.existsSync(POINT_REMOVER_SCRIPT),
			activeJobId,
			queuedJobs: conversionQueue.length,
			cloudCount: clouds.length,
		});
		return true;
	}

	if (request.method === "GET" && url.pathname === "/api/clouds") {
		sendJson(response, 200, { clouds: await listPointClouds() });
		return true;
	}

	if (request.method === "POST" && url.pathname === "/api/conversions") {
		await handleUpload(request, response, url);
		return true;
	}

	if (request.method === "POST" && url.pathname === "/api/segmentations") {
		await handleSegmentation(request, response);
		return true;
	}

	if (request.method === "POST" && url.pathname === "/api/transformations") {
		await handleTransformation(request, response);
		return true;
	}

	if (request.method === "POST" && url.pathname === "/api/point-removals") {
		await handlePointRemoval(request, response);
		return true;
	}

	const jobMatch = url.pathname.match(/^\/api\/conversions\/([0-9a-f-]+)$/i);
	if (request.method === "GET" && jobMatch) {
		const job = jobs.get(jobMatch[1]);
		if (!job) {
			sendJson(response, 404, { error: "Nie znaleziono zadania konwersji." });
		} else {
			sendJson(response, 200, publicJob(job));
		}
		return true;
	}

	if (url.pathname.startsWith("/api/")) {
		sendJson(response, 404, { error: "Nieznany endpoint API." });
		return true;
	}

	return false;
}

async function serveStatic(request, response, url) {
	if (!["GET", "HEAD"].includes(request.method)) {
		sendJson(response, 405, { error: "Niedozwolona metoda HTTP." });
		return;
	}

	let pathname;
	try {
		pathname = decodeURIComponent(url.pathname);
	} catch {
		sendJson(response, 400, { error: "Nieprawidłowy adres URL." });
		return;
	}

	let relativePath = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
	const firstSegment = relativePath.split(/[\\/]/)[0].toLowerCase();
	if ([".git", ".runtime", "node_modules", "tools"].includes(firstSegment)) {
		sendJson(response, 404, { error: "Nie znaleziono pliku." });
		return;
	}

	let filePath = path.resolve(ROOT_DIR, relativePath);
	if (!isPathInside(ROOT_DIR, filePath)) {
		sendJson(response, 403, { error: "Dostęp zabroniony." });
		return;
	}

	let stat;
	try {
		stat = await fsp.stat(filePath);
		if (stat.isDirectory()) {
			filePath = path.join(filePath, "index.html");
			stat = await fsp.stat(filePath);
		}
	} catch {
		sendJson(response, 404, { error: "Nie znaleziono pliku." });
		return;
	}

	if (!stat.isFile()) {
		sendJson(response, 404, { error: "Nie znaleziono pliku." });
		return;
	}

	const headers = {
		"Accept-Ranges": "bytes",
		"Content-Type": MIME_TYPES.get(path.extname(filePath).toLowerCase()) || "application/octet-stream",
		"X-Content-Type-Options": "nosniff",
	};

	let start = 0;
	let end = stat.size - 1;
	let statusCode = 200;
	const range = request.headers.range;

	if (range) {
		const match = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
		if (!match) {
			response.writeHead(416, { "Content-Range": `bytes */${stat.size}` });
			response.end();
			return;
		}

		if (match[1] === "") {
			const suffixLength = Number(match[2]);
			start = Math.max(0, stat.size - suffixLength);
		} else {
			start = Number(match[1]);
		}
		if (match[2] !== "") {
			end = Number(match[2]);
		}

		if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || start >= stat.size) {
			response.writeHead(416, { "Content-Range": `bytes */${stat.size}` });
			response.end();
			return;
		}

		end = Math.min(end, stat.size - 1);
		statusCode = 206;
		headers["Content-Range"] = `bytes ${start}-${end}/${stat.size}`;
	}

	headers["Content-Length"] = end - start + 1;
	response.writeHead(statusCode, headers);
	if (request.method === "HEAD") {
		response.end();
		return;
	}

	const stream = fs.createReadStream(filePath, { start, end });
	stream.on("error", () => response.destroy());
	stream.pipe(response);
}

const server = http.createServer(async (request, response) => {
	try {
		const url = new URL(request.url, `http://localhost:${PORT}`);
		if (await handleApi(request, response, url)) {
			return;
		}
		await serveStatic(request, response, url);
	} catch (error) {
		console.error(error);
		if (!response.headersSent) {
			sendJson(response, 500, { error: "Wewnętrzny błąd serwera." });
		} else {
			response.destroy();
		}
	}
});

async function start() {
	await fsp.mkdir(POINTCLOUDS_DIR, { recursive: true });
	await fsp.mkdir(UPLOADS_DIR, { recursive: true });
	await fsp.mkdir(JOBS_DIR, { recursive: true });

	if (!fs.existsSync(CONVERTER_EXE)) {
		console.warn(`UWAGA: nie znaleziono konwertera: ${CONVERTER_EXE}`);
	}
	if (!fs.existsSync(LASZIP_DLL) || !fs.existsSync(SEGMENTER_SCRIPT)) {
		console.warn("UWAGA: lokalny silnik segmentacji jest niedostępny.");
	}
	if (!fs.existsSync(LASZIP_DLL) || !fs.existsSync(TRANSFORMER_SCRIPT)) {
		console.warn("UWAGA: lokalny silnik transformacji jest niedostępny.");
	}
	if (!fs.existsSync(LASZIP_DLL) || !fs.existsSync(POINT_REMOVER_SCRIPT)) {
		console.warn("UWAGA: lokalny silnik usuwania punktów jest niedostępny.");
	}

	server.listen(PORT, HOST, () => {
		console.log(`Cloud Transformation: http://localhost:${PORT}`);
		console.log(`PotreeConverter: ${fs.existsSync(CONVERTER_EXE) ? "gotowy" : "niedostępny"}`);
		console.log(`Segmentacja LAS/LAZ: ${fs.existsSync(LASZIP_DLL) && fs.existsSync(SEGMENTER_SCRIPT) ? "gotowa" : "niedostępna"}`);
		console.log(`Transformacje LAS/LAZ: ${fs.existsSync(LASZIP_DLL) && fs.existsSync(TRANSFORMER_SCRIPT) ? "gotowe" : "niedostępne"}`);
		console.log(`Usuwanie punktów LAS/LAZ: ${fs.existsSync(LASZIP_DLL) && fs.existsSync(POINT_REMOVER_SCRIPT) ? "gotowe" : "niedostępne"}`);
	});
}

function shutdown() {
	if (activeConverter) {
		activeConverter.kill();
	}
	server.close(() => process.exit(0));
	setTimeout(() => process.exit(1), 3000).unref();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

start().catch(error => {
	console.error(error);
	process.exit(1);
});
