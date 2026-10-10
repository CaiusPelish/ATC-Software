import type { ActionFunctionArgs } from "react-router";
import { getElevenLabsCredentials } from "~/lib/elevenlabs.server";

const warningText =
  "You are showing signs of drowsiness. Please wake up, refocus, and take a safe break as soon as you can.";

export async function action({ request }: ActionFunctionArgs) {
  if (request.method !== "POST") {
    return Response.json(
      { error: "Use POST to request a voice alert." },
      { status: 405 },
    );
  }

  if (request.headers.get("Origin") !== new URL(request.url).origin) {
    return Response.json(
      { error: "Voice alerts must be requested from this app." },
      { status: 403 },
    );
  }

  const { apiKey, voiceId } = getElevenLabsCredentials();
  if (!apiKey || !voiceId) {
    console.warn(
      "[drowsiness voice] Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in the frontend server environment.",
    );
    return Response.json(
      {
        error:
          "ElevenLabs voice alerts are not configured. Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID on the server.",
      },
      { status: 503 },
    );
  }

  let response: Response;
  try {
    response = await fetch(
      `https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voiceId)}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "xi-api-key": apiKey,
          Accept: "audio/mpeg",
        },
        body: JSON.stringify({
          text: warningText,
          model_id: "eleven_multilingual_v2",
          voice_settings: {
            stability: 0.58,
            similarity_boost: 0.72,
          },
        }),
        signal: request.signal,
      },
    );
  } catch (error) {
    if (request.signal.aborted) throw error;
    console.error("[drowsiness voice] Could not connect to ElevenLabs.", error);
    return Response.json(
      { error: "Could not connect to ElevenLabs to generate the voice alert." },
      { status: 502 },
    );
  }

  if (!response.ok) {
    const details = await response.text();
    console.error(
      `[drowsiness voice] ElevenLabs returned HTTP ${response.status}: ${details.slice(0, 400)}`,
    );
    return Response.json(
      {
        error:
          `ElevenLabs could not generate the voice alert (HTTP ${response.status}). Check the server log for details.`,
      },
      { status: 502 },
    );
  }

  const audio = await response.arrayBuffer();
  console.info("[drowsiness voice] ElevenLabs generated a drowsiness warning.");
  return new Response(audio, {
    headers: {
      "Content-Type": "audio/mpeg",
      "Cache-Control": "no-store",
    },
  });
}
