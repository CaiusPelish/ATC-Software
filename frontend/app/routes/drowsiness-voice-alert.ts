import type { ActionFunctionArgs } from "react-router";

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

  const apiKey = process.env.ELEVENLABS_API_KEY;
  const voiceId = process.env.ELEVENLABS_VOICE_ID;
  if (!apiKey || !voiceId) {
    return Response.json(
      {
        error:
          "ElevenLabs voice alerts are not configured. Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID on the server.",
      },
      { status: 503 },
    );
  }

  const response = await fetch(
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

  if (!response.ok) {
    return Response.json(
      {
        error:
          "ElevenLabs could not generate the voice alert. Check the server credentials and voice ID.",
      },
      { status: 502 },
    );
  }

  return new Response(await response.arrayBuffer(), {
    headers: {
      "Content-Type": "audio/mpeg",
      "Cache-Control": "no-store",
    },
  });
}
