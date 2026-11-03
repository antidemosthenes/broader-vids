const token = document.querySelector('meta[name="broader-vids-token"]').content;

async function response(
  path,
  {
    method = "GET",
    body,
    allowFailedStatus = false,
    timeoutMs = method === "GET" ? 30000 : 90000,
  } = {},
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const result = await fetch(path, {
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
    let data;
    try {
      data = await result.json();
    } catch (error) {
      if (controller.signal.aborted) throw error;
      throw Error(
        "The local server did not return a response. Refresh this page.",
      );
    }
    if (!result.ok || (!allowFailedStatus && data.status === "failed"))
      throw Error(data.error || "The request could not complete.");
    return data;
  } catch (error) {
    if (controller.signal.aborted)
      throw Error("The local request timed out. Check the server, then retry.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export const get = (path, options) => response(path, options);
export const request = (path, body, options) =>
  response(path, { ...options, method: "POST", body });
export const post = (body) => request("/api/action", body);
