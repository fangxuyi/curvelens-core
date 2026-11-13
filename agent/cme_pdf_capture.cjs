"use strict";

// Capture through the existing headed browser only. No alternate request,
// reload, or retry: the caller retains its acquisition permission boundary.
async function capturePdf(page, url, { timeoutMs = 45000, minBytes = 100000 } = {}) {
  const observations = [];
  let resolvePdf;
  let timer;
  let finished = false;
  let navigated = false;
  const captured = new Promise((resolve) => { resolvePdf = resolve; });
  const onResponse = async (response) => {
    const headers = response.headers();
    const contentType = headers["content-type"] || "";
    const isPdf = /application\/pdf/i.test(contentType);
    if (response.url() !== url && !isPdf) return;
    const observation = { status: response.status(), contentType };
    if (observations.length < 12) observations.push(observation);
    if (!isPdf) return;
    try {
      const body = await response.body();
      observation.bytes = body.length;
      if (finished) return;
      if (response.status() < 200 || response.status() >= 300) {
        observation.reason = "unsuccessful_http_status";
      } else if (body.length < minBytes || body.subarray(0, 4).toString("utf8") !== "%PDF") {
        observation.reason = "invalid_pdf_bytes";
      } else {
        resolvePdf({ url: response.url(), status: response.status(),
          declaredBytes: Number(headers["content-length"] || 0), body });
      }
    } catch (error) {
      // Do not publish browser internals, response bodies, or arbitrary URLs.
      observation.reason = "response_body_unavailable";
    }
  };
  page.on("response", onResponse);
  const deadline = new Promise((resolve) => {
    timer = setTimeout(() => resolve(null), timeoutMs);
  });
  try {
    // networkidle does not guarantee that asynchronous response.body() handlers
    // have completed, particularly for Chrome's built-in PDF viewer.
    const navigation = page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    // Always observe navigation rejection, even when the PDF wins the race.
    const navigationFailure = navigation.then(() => {
      navigated = true;
      return new Promise(() => {});
    });
    const pdf = await Promise.race([captured, deadline, navigationFailure]);
    if (pdf) return pdf;
    const error = new Error(navigated
      ? "No real PDF bytes captured from CME page within capture deadline"
      : `page.goto: Timeout ${timeoutMs}ms awaiting CME navigation`);
    error.code = navigated ? "CME_PDF_CAPTURE_FAILED" : "CME_PDF_NAVIGATION_TIMEOUT";
    error.diagnostics = { timeoutMs, responses: observations };
    throw error;
  } finally {
    finished = true;
    clearTimeout(timer);
    page.off("response", onResponse);
  }
}

module.exports = { capturePdf };
