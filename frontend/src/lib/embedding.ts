import "server-only";
import OpenAI from "openai";

// 仕様6.4: OpenAI text-embedding-3-small(1536次元)で確定
const EMBEDDING_MODEL = "text-embedding-3-small";

let client: OpenAI | null = null;

function getClient() {
  if (!client) client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
  return client;
}

export async function embedText(text: string): Promise<number[]> {
  const response = await getClient().embeddings.create({
    model: EMBEDDING_MODEL,
    input: text.slice(0, 6000),
  });
  return response.data[0].embedding;
}
