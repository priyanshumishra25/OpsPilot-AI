/** Minimal Server-Sent Events reader over fetch (EventSource cannot send Authorization headers). */
export interface SseMessage {
  event: string;
  data: string;
}

export function parseSseChunk(buffer: string): { messages: SseMessage[]; rest: string } {
  const normalised = buffer.replace(/\r\n/g, "\n");
  const blocks = normalised.split("\n\n");
  const rest = blocks.pop() ?? "";
  const messages: SseMessage[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) messages.push({ event, data: data.join("\n") });
  }
  return { messages, rest };
}

export async function* readSse(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SseMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { messages, rest } = parseSseChunk(buffer);
      buffer = rest;
      yield* messages;
    }
    const { messages } = parseSseChunk(buffer + "\n\n");
    yield* messages;
  } finally {
    reader.releaseLock();
  }
}
