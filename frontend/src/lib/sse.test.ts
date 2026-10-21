import { parseSseChunk, readSse } from "./sse";

describe("parseSseChunk", () => {
  it("parses complete events and keeps the partial remainder", () => {
    const { messages, rest } = parseSseChunk('event: text\r\ndata: {"text":"a"}\r\n\r\n: ping\n\nevent: done\ndata: {"x"');
    expect(messages).toEqual([{ event: "text", data: '{"text":"a"}' }]);
    expect(rest).toBe('event: done\ndata: {"x"');
  });

  it("joins multi-line data", () => {
    expect(parseSseChunk("data: a\ndata: b\n\n").messages).toEqual([{ event: "message", data: "a\nb" }]);
  });
});

describe("readSse", () => {
  it("reassembles events split across network chunks", async () => {
    const parts = ['event: text\ndata: {"te', 'xt":"hi"}\n', "\nevent: done\ndata: {}\n\n"];
    const stream = new ReadableStream<Uint8Array>({
      start(c) { for (const p of parts) c.enqueue(new TextEncoder().encode(p)); c.close(); },
    });
    const out = [];
    for await (const m of readSse(stream)) out.push(m);
    expect(out).toEqual([{ event: "text", data: '{"text":"hi"}' }, { event: "done", data: "{}" }]);
  });
});
