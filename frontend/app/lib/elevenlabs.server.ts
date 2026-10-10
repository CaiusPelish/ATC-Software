import "dotenv/config";

export function getElevenLabsCredentials() {
  return {
    apiKey: process.env.ELEVENLABS_API_KEY,
    voiceId: process.env.ELEVENLABS_VOICE_ID,
  };
}
