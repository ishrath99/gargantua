/**
 * SSE consumer tests.
 *
 * We exercise the framing parser (``parseSSE``) directly against
 * synthetic ``ReadableStream``s — no fetch involved — so the tests
 * are hermetic and fast.  The transport layer (``streamRun``) is
 * one ``fetch`` call thick and is covered by the e2e smoke.
 */

import { describe, expect, it, vi } from 'vitest';

import { parseSSE, type SSEEvent } from '@/lib/chat/sse';

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) {
        controller.enqueue(encoder.encode(c));
      }
      controller.close();
    },
  });
}

async function drain(
  body: ReadableStream<Uint8Array>,
): Promise<SSEEvent[]> {
  const out: SSEEvent[] = [];
  for await (const e of parseSSE(body)) out.push(e);
  return out;
}

describe('parseSSE', () => {
  it('parses a single data frame', async () => {
    const body = streamOf(
      'data: {"event":"RunStarted","run_id":"r1"}\n\n',
      'data: [DONE]\n\n',
    );
    const events = await drain(body);
    expect(events).toHaveLength(1);
    expect(events[0].data).toEqual({ event: 'RunStarted', run_id: 'r1' });
  });

  it('parses multiple frames in one chunk', async () => {
    const body = streamOf(
      'data: {"event":"RunContent","content":"hi"}\n\n' +
        'data: {"event":"RunContent","content":"!"}\n\n' +
        'data: [DONE]\n\n',
    );
    const events = await drain(body);
    expect(events.map((e) => (e.data as { content: string }).content)).toEqual(['hi', '!']);
  });

  it('joins a frame split across chunks', async () => {
    const body = streamOf(
      'data: {"event":"RunContent","conte',
      'nt":"hello"}\n',
      '\ndata: [DONE]\n\n',
    );
    const events = await drain(body);
    expect(events).toHaveLength(1);
    expect((events[0].data as { content: string }).content).toBe('hello');
  });

  it('stops at the [DONE] sentinel', async () => {
    // Anything after [DONE] must not be yielded — the server contract
    // says [DONE] terminates the stream.
    const body = streamOf(
      'data: {"event":"RunContent","content":"a"}\n\n',
      'data: [DONE]\n\n',
      'data: {"event":"RunContent","content":"after"}\n\n',
    );
    const events = await drain(body);
    expect(events).toHaveLength(1);
  });

  it('ignores SSE comments and blank lines', async () => {
    const body = streamOf(
      ': keep-alive\n\n',
      'data: {"event":"RunContent","content":"x"}\n\n',
      '\n\n',
      'data: [DONE]\n\n',
    );
    const events = await drain(body);
    expect(events).toHaveLength(1);
    expect((events[0].data as { content: string }).content).toBe('x');
  });

  it('joins multi-line data: fields with \\n', async () => {
    // Per the SSE spec, two ``data:`` lines in one frame are joined
    // by a literal newline.  We construct a payload whose JSON spans
    // two ``data:`` lines.
    const body = streamOf('data: {\ndata: "event":"RunContent","content":"y"}\n\n', 'data: [DONE]\n\n');
    const events = await drain(body);
    expect(events).toHaveLength(1);
    expect((events[0].data as { content: string }).content).toBe('y');
  });

  it('skips malformed JSON frames without throwing', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const body = streamOf(
      'data: not-json\n\n',
      'data: {"event":"RunContent","content":"ok"}\n\n',
      'data: [DONE]\n\n',
    );
    const events = await drain(body);
    expect(events).toHaveLength(1);
    expect((events[0].data as { content: string }).content).toBe('ok');
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('normalizes CRLF to LF before framing', async () => {
    const body = streamOf(
      'data: {"event":"RunContent","content":"z"}\r\n\r\n',
      'data: [DONE]\r\n\r\n',
    );
    const events = await drain(body);
    expect(events).toHaveLength(1);
    expect((events[0].data as { content: string }).content).toBe('z');
  });

  it('handles a multi-byte UTF-8 char split across chunks', async () => {
    const encoder = new TextEncoder();
    // "é" (U+00E9) is 0xC3 0xA9 in UTF-8 — split the two bytes.
    const all = encoder.encode('data: {"event":"RunContent","content":"é"}\n\ndata: [DONE]\n\n');
    const split = all.length - 12; // somewhere inside the payload byte sequence
    const left = all.slice(0, split);
    const right = all.slice(split);
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(left);
        c.enqueue(right);
        c.close();
      },
    });
    const events = await drain(body);
    expect(events).toHaveLength(1);
    expect((events[0].data as { content: string }).content).toBe('é');
  });

  it('returns no events when the stream is empty', async () => {
    const events = await drain(streamOf());
    expect(events).toEqual([]);
  });
});
