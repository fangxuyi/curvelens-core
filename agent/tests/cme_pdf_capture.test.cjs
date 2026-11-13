"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { capturePdf } = require("../cme_pdf_capture.cjs");
const url = "https://www.cmegroup.com/daily_bulletin/current/section.pdf";
const body = Buffer.from("%PDF-test-document");
function response({ type = "application/pdf", status = 200, data = body, delay = 0, fail = false } = {}) {
  return { headers: () => ({ "content-type": type }), status: () => status,
    url: () => url, body: async () => {
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (fail) throw new Error("private browser diagnostic");
      return data;
    } };
}
function pageFor(responses, navigationError) {
  const page = new EventEmitter();
  page.goto = async () => {
    for (const item of responses) page.emit("response", item);
    if (navigationError) throw navigationError;
  };
  return page;
}
test("waits for PDF body after navigation completes and removes listener", async () => {
  const page = pageFor([response({ delay: 35 })]);
  assert.deepEqual((await capturePdf(page, url, { timeoutMs: 200, minBytes: 5 })).body, body);
  assert.equal(page.listenerCount("response"), 0);
});
test("records HTTP and content-type evidence when CME returns HTML", async () => {
  const page = pageFor([response({ type: "text/html", status: 403 })]);
  await assert.rejects(capturePdf(page, url, { timeoutMs: 15 }), (error) => {
    assert.equal(error.code, "CME_PDF_CAPTURE_FAILED");
    assert.deepEqual(error.diagnostics.responses, [{ status: 403, contentType: "text/html" }]);
    return true;
  });
  assert.equal(page.listenerCount("response"), 0);
});
test("rejects small/invalid PDF and unsuccessful HTTP responses", async () => {
  for (const options of [{ data: Buffer.from("html-error") }, { status: 503 }, { data: Buffer.from("%PDF") }]) {
    await assert.rejects(capturePdf(pageFor([response(options)]), url,
      { timeoutMs: 15, minBytes: 5 }), { code: "CME_PDF_CAPTURE_FAILED" });
  }
});
test("records body-read failure without leaking the browser exception", async () => {
  await assert.rejects(capturePdf(pageFor([response({ fail: true })]), url,
    { timeoutMs: 15 }), (error) => {
    assert.equal(error.diagnostics.responses[0].reason, "response_body_unavailable");
    assert.ok(!JSON.stringify(error.diagnostics).includes("private"));
    return true;
  });
});
test("propagates navigation errors for the existing bounded retry classifier", async () => {
  const error = new Error("net::ERR_CONNECTION_RESET");
  await assert.rejects(capturePdf(pageFor([], error), url), (actual) => actual === error);
});
test("stops waiting for stalled bodies at the deadline", async () => {
  const stalled = response();
  stalled.body = () => new Promise(() => {});
  await assert.rejects(capturePdf(pageFor([stalled]), url, { timeoutMs: 15 }),
    { code: "CME_PDF_CAPTURE_FAILED" });
});
test("navigation deadline retains the host's transient timeout classification", async () => {
  const page = pageFor([]);
  page.goto = () => new Promise(() => {});
  await assert.rejects(capturePdf(page, url, { timeoutMs: 15 }), (error) => {
    assert.equal(error.code, "CME_PDF_NAVIGATION_TIMEOUT");
    assert.match(error.message, /page.goto: Timeout/);
    return true;
  });
});
