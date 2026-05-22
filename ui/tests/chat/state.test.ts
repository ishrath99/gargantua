/**
 * Chat-state reducer tests.
 *
 * The reducer is pure, so we drive it with arrays of agno events
 * and assert on the resulting :type:`ChatThreadState`.
 */

import { describe, expect, it } from 'vitest';

import type { AgnoRunEvent } from '@/lib/chat/events';
import {
  INITIAL_THREAD_STATE,
  chatReducer,
  type ChatThreadState,
} from '@/lib/chat/state';

function send(state: ChatThreadState, input: string): ChatThreadState {
  return chatReducer(state, {
    type: 'send',
    userMessageId: 'u1',
    assistantMessageId: 'a1',
    input,
  });
}

function apply(state: ChatThreadState, ...events: AgnoRunEvent[]): ChatThreadState {
  let next = state;
  for (const e of events) {
    next = chatReducer(next, { type: 'event', event: e });
  }
  return next;
}

describe('chatReducer', () => {
  it('send pushes a user + streaming assistant message', () => {
    const s = send(INITIAL_THREAD_STATE, 'hi there');
    expect(s.messages).toHaveLength(2);
    expect(s.messages[0]).toMatchObject({ role: 'user', content: 'hi there' });
    expect(s.messages[1]).toMatchObject({
      role: 'assistant',
      content: '',
      streaming: true,
    });
    expect(s.isStreaming).toBe(true);
  });

  it('accumulates RunContent deltas into the assistant message', () => {
    const sent = send(INITIAL_THREAD_STATE, 'hi');
    const out = apply(
      sent,
      { event: 'RunStarted', run_id: 'r1', session_id: 'sess-1' },
      { event: 'RunContent', content: 'hel' },
      { event: 'RunContent', content: 'lo' },
      { event: 'RunContent', content: ' world' },
    );
    expect(out.messages[1].content).toBe('hello world');
    expect(out.messages[1].streaming).toBe(true);
    expect(out.messages[1].runId).toBe('r1');
    expect(out.sessionId).toBe('sess-1');
  });

  it('RunCompleted flips streaming off and keeps the longer text', () => {
    const sent = send(INITIAL_THREAD_STATE, 'hi');
    const out = apply(
      sent,
      { event: 'RunContent', content: 'hello' },
      // Final content is the cumulative buffer — should NOT
      // double-append; reducer keeps whichever is longer.
      { event: 'RunCompleted', content: 'hello world' },
    );
    expect(out.messages[1].content).toBe('hello world');
    expect(out.messages[1].streaming).toBe(false);
    expect(out.isStreaming).toBe(true); // still in the stream-end action's hands
  });

  it('RunCompleted with a shorter buffer leaves the deltas intact', () => {
    const sent = send(INITIAL_THREAD_STATE, 'hi');
    const out = apply(
      sent,
      { event: 'RunContent', content: 'hello world from gargantua' },
      { event: 'RunCompleted', content: 'hello' },
    );
    expect(out.messages[1].content).toBe('hello world from gargantua');
  });

  it('RunError sets the message error and stops streaming', () => {
    const sent = send(INITIAL_THREAD_STATE, 'hi');
    const out = apply(sent, { event: 'RunError', content: 'rate limited' });
    expect(out.messages[1].error).toBe('rate limited');
    expect(out.messages[1].streaming).toBe(false);
  });

  it('threads tool-call started/completed events under the assistant turn', () => {
    const sent = send(INITIAL_THREAD_STATE, 'list issues');
    const out = apply(
      sent,
      {
        event: 'ToolCallStarted',
        tool: {
          tool_call_id: 'tc-1',
          tool_name: 'github_search',
          tool_args: { q: 'is:open' },
        },
      },
      {
        event: 'ToolCallCompleted',
        tool: {
          tool_call_id: 'tc-1',
          tool_name: 'github_search',
          tool_args: { q: 'is:open' },
          result: '[1, 2, 3]',
        },
      },
    );
    expect(out.messages[1].toolCalls).toHaveLength(1);
    expect(out.messages[1].toolCalls[0]).toMatchObject({
      id: 'tc-1',
      name: 'github_search',
      status: 'completed',
      args: { q: 'is:open' },
      result: '[1, 2, 3]',
    });
  });

  it('ToolCallError marks the call as errored', () => {
    const sent = send(INITIAL_THREAD_STATE, 'do thing');
    const out = apply(
      sent,
      { event: 'ToolCallStarted', tool: { tool_call_id: 'x', tool_name: 'k8s_apply' } },
      { event: 'ToolCallError', tool: { tool_call_id: 'x' }, error: 'permission denied' },
    );
    expect(out.messages[1].toolCalls[0].status).toBe('error');
    expect(out.messages[1].toolCalls[0].error).toBe('permission denied');
  });

  it('stream_end action flips isStreaming and clears the assistant streaming flag', () => {
    const sent = send(INITIAL_THREAD_STATE, 'hi');
    const out = chatReducer(sent, { type: 'stream_end' });
    expect(out.isStreaming).toBe(false);
    expect(out.messages[1].streaming).toBe(false);
  });

  it('transport_error sets the banner and ends streaming', () => {
    const sent = send(INITIAL_THREAD_STATE, 'hi');
    const out = chatReducer(sent, {
      type: 'transport_error',
      message: '503 Service Unavailable',
    });
    expect(out.transportError).toBe('503 Service Unavailable');
    expect(out.isStreaming).toBe(false);
  });

  it('clears the transport_error on the next send', () => {
    const sent1 = send(INITIAL_THREAD_STATE, 'first');
    const errored = chatReducer(sent1, {
      type: 'transport_error',
      message: 'boom',
    });
    const sent2 = chatReducer(errored, {
      type: 'send',
      userMessageId: 'u2',
      assistantMessageId: 'a2',
      input: 'try again',
    });
    expect(sent2.transportError).toBeNull();
  });

  it('ignores events when there is no assistant turn in flight', () => {
    const out = apply(INITIAL_THREAD_STATE, { event: 'RunContent', content: 'x' });
    expect(out).toBe(INITIAL_THREAD_STATE);
  });

  it('reset returns to the initial state', () => {
    const sent = send(INITIAL_THREAD_STATE, 'hi');
    const out = chatReducer(sent, { type: 'reset' });
    expect(out).toBe(INITIAL_THREAD_STATE);
  });
});
