const token = document.querySelector('meta[name="broader-vids-token"]').content;

async function response(
  path,
  {
    method = "GET",
    body,
    allowFailedStatus = false,
    unconfirmedMessage,
    timeoutMs = method === "GET" ? 30000 : 90000,
  } = {},
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let result, data;
  try {
    result = await fetch(path, {
      method,
      cache: "no-store",
      signal: controller.signal,
      ...(method === "POST"
        ? {
            headers: {
              "Content-Type": "application/json",
              "X-Broader-Vids-Token": token,
            },
            body: JSON.stringify(body),
          }
        : {}),
    });
    data = await result.json();
  } catch (error) {
    if (unconfirmedMessage) throw Error(unconfirmedMessage);
    if (controller.signal.aborted)
      throw Error("The local request timed out. Check the server, then retry.");
    if (result)
      throw Error(
        "The local server did not return a response. Refresh this page.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
  if (!result.ok || (!allowFailedStatus && data.status === "failed"))
    throw Error(data.error || "The request could not complete.");
  return data;
}

export const get = (path, options) => response(path, options);
export const request = (path, body, options) =>
  response(path, { ...options, method: "POST", body });
export const post = (body) => request("/api/action", body);
