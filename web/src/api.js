const json = async (res) => {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ? JSON.stringify(body.detail) : res.statusText);
  }
  return body;
};

// X-Api-Key, если задан на сборке (VITE_API_KEY через build arg).
// Для тестового стенда ключ лежит в бандле открыто — он защищает API от
// постороннего прямого использования, а не от посетителей страницы.
const API_KEY = import.meta.env.VITE_API_KEY || "";
const authHeaders = () => (API_KEY ? { "X-Api-Key": API_KEY } : {});

export const api = {
  async start(phone) {
    return json(
      await fetch("/api/verify/start", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ phone }),
      })
    );
  },

  async status(verificationId) {
    return json(
      await fetch(`/api/verify/${verificationId}/status`, { headers: authHeaders() })
    );
  },

  async simulate(verificationId) {
    return json(
      await fetch("/api/dev/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ verification_id: verificationId }),
      })
    );
  },
};
