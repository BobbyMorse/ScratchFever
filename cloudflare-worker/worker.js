const OCR_PROMPT = `Analyze this lottery scratch-off ticket image and return ONLY valid JSON — no other text — \
matching this schema exactly:
{
  "rawText": "all text visible on the ticket",
  "gameName": "game title from the ticket or null",
  "ticketNumber": "serial/ticket number if visible or null",
  "gameType": "number_match | bingo | instant_win | unknown",
  "winningNumbers": [integers],
  "yourNumbers": [integers — the player's revealed numbers],
  "callerNumbers": [integers — for bingo: drawn/called numbers],
  "bingoCard": [integers — numbers printed on the bingo card],
  "autoWinSymbol": true or false,
  "multiplier": integer or null,
  "multiplierPrize": number or null,
  "bonusWins": [prize amounts as numbers],
  "won": true / false / null,
  "winReason": "brief explanation or null",
  "prizeHint": number or null
}
gameType rules:
- number_match: ticket has winning numbers and player numbers to compare
- bingo: bingo card with caller/drawn numbers
- instant_win: prizes revealed directly (no number comparison)
Set won=null when you cannot confidently determine the result.`;

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  });
}

// Replicates backend/users.py create_token / decode_token:
// token = {base64url_payload}.{hmac_sha256_hex}
async function verifyToken(token, secret) {
  const lastDot = token.lastIndexOf('.');
  if (lastDot === -1) return null;
  const b64 = token.slice(0, lastDot);
  const sig = token.slice(lastDot + 1);
  try {
    const key = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const sigBuf = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(b64));
    const expected = Array.from(new Uint8Array(sigBuf))
      .map(b => b.toString(16).padStart(2, '0')).join('');

    if (sig.length !== expected.length) return null;
    let diff = 0;
    for (let i = 0; i < sig.length; i++) diff |= sig.charCodeAt(i) ^ expected.charCodeAt(i);
    if (diff !== 0) return null;

    const padding = '='.repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(b64.replace(/-/g, '+').replace(/_/g, '/') + padding));
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
    if (request.method !== 'POST') return json({ detail: 'Method not allowed' }, 405);

    const authHeader = request.headers.get('Authorization') || '';
    const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
    if (!token) return json({ detail: 'Login required' }, 401);

    const user = await verifyToken(token, env.SECRET_KEY);
    if (!user) return json({ detail: 'Invalid or expired token — please log in again' }, 401);

    let body;
    try { body = await request.json(); } catch { return json({ detail: 'Invalid JSON body' }, 400); }
    if (!body.image) return json({ detail: 'Missing image' }, 400);

    let raw = body.image;
    let mimeType = 'image/jpeg';
    if (raw.startsWith('data:')) {
      const [header, data] = raw.split(',', 2);
      raw = data;
      if (header.includes('png')) mimeType = 'image/png';
      else if (header.includes('webp')) mimeType = 'image/webp';
      else if (header.includes('gif')) mimeType = 'image/gif';
    }

    const geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${env.GOOGLE_GEMINI_API_KEY}`;
    let geminiRes;
    try {
      geminiRes = await fetch(geminiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contents: [{ parts: [
            { inline_data: { mime_type: mimeType, data: raw } },
            { text: OCR_PROMPT },
          ]}],
        }),
      });
    } catch (e) {
      return json({ detail: `Gemini request failed: ${e.message}` }, 502);
    }

    if (!geminiRes.ok) {
      const errText = await geminiRes.text();
      return json({ detail: `Gemini API error: ${errText}` }, 502);
    }

    const geminiData = await geminiRes.json();
    const responseText = geminiData.candidates?.[0]?.content?.parts?.[0]?.text || '';

    const jsonMatch = responseText.match(/\{[\s\S]*\}/);
    if (!jsonMatch) return json({ detail: 'Could not parse OCR response' }, 422);

    let data;
    try { data = JSON.parse(jsonMatch[0]); }
    catch { return json({ detail: 'Invalid JSON from OCR' }, 422); }

    const ints = k => (data[k] || []).filter(n => n != null).map(Number);
    const winning = ints('winningNumbers');
    const yours = ints('yourNumbers');

    return json({
      rawText: data.rawText || responseText,
      scannedGameName: data.gameName || null,
      matchedGame: null,
      matchedGameScore: 0,
      ticketNumber: data.ticketNumber || null,
      analysis: {
        gameType: data.gameType || 'unknown',
        winningNumbers: winning,
        yourNumbers: yours,
        matchedNumbers: yours.filter(n => winning.includes(n)),
        matchedPrize: data.matchedPrize || null,
        multiplier: data.multiplier || null,
        multiplierPrize: data.multiplierPrize || null,
        callerNumbers: ints('callerNumbers'),
        bingoCard: ints('bingoCard'),
        autoWinSymbol: Boolean(data.autoWinSymbol),
        bonusWins: (data.bonusWins || []).filter(n => n != null).map(Number),
        won: data.won ?? null,
        winReason: data.winReason || null,
        prizeHint: data.prizeHint || null,
      },
    });
  },
};
