import {statusLabel} from "./common.js";

const collator = new Intl.Collator("pl", {numeric: true, sensitivity: "base"});
const compareColumns = {
  name: (left, right) => collator.compare(left.name, right.name),
  status: (left, right) => collator.compare(statusLabel(left.status), statusLabel(right.status)),
  visibility: (left, right) => collator.compare(left.visibility, right.visibility),
  sizeBytes: (left, right) => left.sizeBytes - right.sizeBytes,
  createdAt: (left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt),
};

export function sortClouds(clouds, column, direction) {
  const compare = compareColumns[column];
  const order = direction === "descending" ? -1 : 1;
  return [...clouds].sort((left, right) => order * compare(left, right)
    || Date.parse(right.createdAt) - Date.parse(left.createdAt)
    || collator.compare(left.id, right.id));
}
