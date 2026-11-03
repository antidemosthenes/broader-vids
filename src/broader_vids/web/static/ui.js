export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );

export function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function button(text, action, className = "button") {
  const node = el("button", className, text);
  node.type = "button";
  node.onclick = action;
  return node;
}

export function link(text, url, className = "") {
  const node = el("a", className, text);
  node.href = url;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}

export function dateLabel(value, precise = false) {
  const date = new Date(value);
  if (!value || !Number.isFinite(date.getTime())) return "Never";
  return new Intl.DateTimeFormat(
    undefined,
    precise
      ? {
          year: "numeric",
          month: "long",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
          second: "2-digit",
          timeZoneName: "short",
        }
      : { dateStyle: "medium", timeStyle: "short" },
  ).format(date);
}

export const platformName = (platform) =>
  platform === "youtube" ? "YouTube" : "Bilibili";
export const number = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? Math.round(value).toString()
    : "—";
