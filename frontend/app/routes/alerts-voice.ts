import type { ActionFunctionArgs } from "react-router";
import { getElevenLabsCredentials } from "~/lib/elevenlabs.server";

// Spoken conflict alerts. The browser sends the phrase; the ElevenLabs key stays here.
// Only short plain-text phrases are accepted, so this can't be used to spend credits
// on arbitrary text, and each phrase is generated once and then served from memory.
const MAX_CHARS = 200;
const SAFE_TEXT = /^[A-Za-z0-9 ,.'-]+$/;
const cache = new Map<string, ArrayBuffer>();
const CACHE_SIZE = 50;

export async function action({ request }: ActionFunctionArgs) {
  if (request.method !== "POST") {
    return Response.json({ error: "Use POST to request a voice alert." }, { status: 405 });
  }
  if (request.headers.get("Origin") !== new URL(request.url).origin) {
    return Response.json({ error: "Voice alerts must be requested from this app." }, { status: 403 });
  }

  const body = (await request.json().catch(() => null)) as { text?: unknown } | null;
  const text = typeof body?.text === "string" ? body.text.trim() : "";
  if (!text || text.length > MAX_CHARS || !SAFE_TEXT.test(text)) {
    return Response.json({ error: "Alert text must be a short plain sentence." }, { status: 400 });
  }

  const cached = cache.get(text);
  if (cached) return audioResponse(cached);

  const { apiKey, voiceId } = getElevenLabsCredentials();
  if (!apiKey || !voiceId) {
    return Response.json(
      { error: "ElevenLabs voice alerts are not configured. Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in frontend/.env." },
      { status: 503 },
    );
  }

  let response: Response;
  try {
    response = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voiceId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "xi-api-key": apiKey, Accept: "audio/mpeg" },
      body: JSON.stringify({
        text,
        model_id: "eleven_multilingual_v2",
        voice_settings: { stability: 0.62, similarity_boost: 0.72 },
      }),
      signal: request.signal,
    });
  } catch (error) {
    if (request.signal.aborted) throw error;
    console.error("[alert voice] Could not connect to ElevenLabs.", error);
    return Response.json({ error: "Could not connect to ElevenLabs." }, { status: 502 });
  }

  if (!response.ok) {
    console.error(`[alert voice] ElevenLabs returned HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`);
    return Response.json({ error: `ElevenLabs could not generate the alert (HTTP ${response.status}).` }, { status: 502 });
  }

  const audio = await response.arrayBuffer();
  if (cache.size >= CACHE_SIZE) cache.delete(cache.keys().next().value!);
  cache.set(text, audio);
  console.info(`[alert voice] Generated: "${text}"`);
  return audioResponse(audio);
}

const audioResponse = (audio: ArrayBuffer) =>
  new Response(audio, { headers: { "Content-Type": "audio/mpeg", "Cache-Control": "no-store" } });
