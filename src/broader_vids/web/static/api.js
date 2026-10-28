const token = document.querySelector('meta[name="broader-vids-token"]').content;

async function response(request) {
  const result = await request;
  let data;
  try {
    data = await result.json();
  } catch {
    throw Error(
      "The local server did not return a response. Refresh this page.",
    );
  }
  if (!result.ok || data.status === "failed")
    throw Error(data.error || "The request could not complete.");
  return data;
}

export const get = (path) => response(fetch(path, { cache: "no-store" }));
export const request = (path, body) =>
  response(
    fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Broader-Vids-Token": token,
      },
      body: JSON.stringify(body),
    }),
  );
export const post = (body) => request("/api/action", body);
