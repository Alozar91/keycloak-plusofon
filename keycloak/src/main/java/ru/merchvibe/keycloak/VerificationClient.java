package ru.merchvibe.keycloak;

import org.keycloak.util.JsonSerialization;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

/**
 * Клиент сервиса верификации (FastAPI): POST /api/verify/start, GET /api/verify/{id}/status.
 */
class VerificationClient {

    record Start(String verificationId, String callNumber, int ttl) { }
    record Status(String verificationId, String status, Integer ttlLeft) { }

    private final HttpClient http = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1) // без этого h2c-upgrade ломает body у uvicorn/FastAPI
            .connectTimeout(Duration.ofSeconds(3))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();

    private final String baseUrl;
    private final String apiKey;

    VerificationClient(String baseUrl, String apiKey) {
        if (baseUrl.endsWith("/")) baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
    }

    /** Начать верификацию номера. Бросает IOException при любой ошибке сервиса. */
    Start start(String phone, String sessionRef) throws IOException, InterruptedException {
        Map<String, Object> body = new HashMap<>();
        body.put("phone", phone);
        if (sessionRef != null) body.put("session_ref", sessionRef);
        Map<String, Object> resp = post(baseUrl + "/api/verify/start", body);
        return new Start(
                str(resp, "verification_id"),
                str(resp, "call_number"),
                resp.get("ttl") instanceof Number n ? n.intValue() : 180);
    }

    /** Статус верификации. status: pending | verified | expired (404 → expired). */
    Status status(String verificationId) throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + "/api/verify/" + verificationId + "/status"))
                .timeout(Duration.ofSeconds(4))
                .header("Accept", "application/json")
                .header("X-Api-Key", apiKey)
                .GET().build();
        HttpResponse<byte[]> r = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
        if (r.statusCode() == 404) return new Status(verificationId, "expired", 0);
        if (r.statusCode() != 200) throw new IOException("status HTTP " + r.statusCode());
        Map<String, Object> m = parse(r.body());
        return new Status(verificationId, str(m, "status"),
                m.get("ttl_left") instanceof Number n ? n.intValue() : null);
    }

    private Map<String, Object> post(String url, Map<String, Object> body)
            throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                .timeout(Duration.ofSeconds(10))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .header("X-Api-Key", apiKey)
                .POST(HttpRequest.BodyPublishers.ofByteArray(JsonSerialization.writeValueAsBytes(body)))
                .build();
        HttpResponse<byte[]> r = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
        if (r.statusCode() != 200) {
            throw new IOException("start HTTP " + r.statusCode() + ": "
                    + new String(r.body(), java.nio.charset.StandardCharsets.UTF_8));
        }
        return parse(r.body());
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> parse(byte[] raw) throws IOException {
        Object o = JsonSerialization.readValue(raw, Map.class);
        if (o instanceof Map) return (Map<String, Object>) o;
        throw new IOException("unexpected response type");
    }

    private static String str(Map<String, Object> m, String key) {
        Object v = m.get(key);
        return v == null ? null : String.valueOf(v);
    }
}
