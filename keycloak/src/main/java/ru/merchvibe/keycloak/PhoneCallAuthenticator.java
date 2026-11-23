package ru.merchvibe.keycloak;

import org.jboss.logging.Logger;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.Authenticator;
import org.keycloak.forms.login.LoginFormsProvider;
import org.keycloak.sessions.AuthenticationSessionModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

import java.util.stream.Stream;

/**
 * Аутентификатор «вход по звонку».
 *
 * Состояния (в auth notes): phoneVerifyId / phoneVerifyPhone / phoneVerifyCallNumber.
 *   1) нет заметок + нет формы      → страница ввода телефона
 *   2) есть форма (phone=...)       → POST /api/verify/start → страница «позвоните на номер»
 *                                     (JS автосабмит каждые 4 с)
 *   3) есть заметки                 → GET status; verified → найти/создать пользователя → success;
 *                                     expired → заметки сбросить, снова форма телефона
 */
public class PhoneCallAuthenticator implements Authenticator {

    private static final Logger log = Logger.getLogger(PhoneCallAuthenticator.class);

    public static final String NOTE_ID = "phoneVerifyId";
    public static final String NOTE_PHONE = "phoneVerifyPhone";
    public static final String NOTE_CALL = "phoneVerifyCallNumber";
    public static final String NOTE_STARTED = "phoneVerifyStarted";
    public static final String NOTE_TTL = "phoneVerifyTtl";

    public static final String ATTR_PHONE = "phoneNumber";

    @Override
    public void authenticate(AuthenticationFlowContext ctx) {
        process(ctx);
    }

    @Override
    public void action(AuthenticationFlowContext ctx) {
        process(ctx);
    }

    @Override
    public void setRequiredActions(KeycloakSession session, RealmModel realm, UserModel user) { }

    @Override
    public boolean requiresUser() {
        return false;
    }

    @Override
    public boolean configuredFor(KeycloakSession session, RealmModel realm, UserModel user) {
        return false;
    }

    @Override
    public void close() { }

    private void process(AuthenticationFlowContext ctx) {
        AuthenticationSessionModel session = ctx.getAuthenticationSession();
        VerificationClient client = client(ctx);
        String userMode = cfg(ctx, "userMode", "create");

        String verificationId = session.getAuthNote(NOTE_ID);
        if (verificationId != null && !verificationId.isBlank()) {
            // --- фаза 2: проверяем статус ---
            try {
                VerificationClient.Status st = client.status(verificationId);
                log.infof("phone verify status: id=%s status=%s", verificationId, st.status());
                switch (st.status()) {
                    case "verified" -> {
                        if (succeed(ctx, session, userMode)) return;
                        clearNotes(session);
                        challengePhoneForm(ctx, null);
                    }
                    case "expired" -> {
                        clearNotes(session);
                        challengePhoneForm(ctx, "Время ожидания звонка истекло. Начните заново.");
                    }
                    default -> challengeCallPage(ctx, session);
                }
            } catch (Exception e) {
                log.warn("verification status check failed", e);
                clearNotes(session);
                challengePhoneForm(ctx, "Сервис верификации недоступен. Попробуйте ещё раз.");
            }
            return;
        }

        String phoneRaw = ctx.getHttpRequest().getDecodedFormParameters().getFirst("phone");
        if (phoneRaw != null && !phoneRaw.isBlank()) {
            // --- фаза 1.5: старт верификации ---
            String phone = normalizePhone(phoneRaw);
            if (phone == null) {
                challengePhoneForm(ctx, "Некорректный номер телефона. Формат: +7XXXXXXXXXX");
                return;
            }
            try {
                VerificationClient.Start start = client.start(phone, session.getParentSession().getId());
                session.setAuthNote(NOTE_ID, start.verificationId());
                session.setAuthNote(NOTE_PHONE, phone);
                session.setAuthNote(NOTE_CALL, start.callNumber());
                session.setAuthNote(NOTE_STARTED, String.valueOf(System.currentTimeMillis() / 1000));
                session.setAuthNote(NOTE_TTL, String.valueOf(start.ttl()));
                log.infof("phone verification started: phone=%s id=%s",
                        maskPhone(phone), start.verificationId());
                challengeCallPage(ctx, session);
            } catch (Exception e) {
                log.error("verification start failed", e);
                challengePhoneForm(ctx, "Не удалось запустить проверку. Попробуйте ещё раз.");
            }
        } else {
            // --- фаза 1: форма ввода номера ---
            challengePhoneForm(ctx, null);
        }
    }

    /** verified: найти или создать пользователя, завершить аутентификацию. */
    private boolean succeed(AuthenticationFlowContext ctx, AuthenticationSessionModel session, String userMode) {
        RealmModel realm = ctx.getRealm();
        String phone = session.getAuthNote(NOTE_PHONE);
        KeycloakSession kc = ctx.getSession();
        UserModel user = findByPhone(kc.users().searchForUserByUserAttributeStream(realm, ATTR_PHONE, phone));
        if (user == null) {
            if ("require".equals(userMode)) {
                challengePhoneForm(ctx, "Пользователь с таким номером не зарегистрирован.");
                return false;
            }
            try {
                user = kc.users().addUser(realm, phone);
                user.setEnabled(true);
                user.setSingleAttribute(ATTR_PHONE, phone);
                log.infof("created user by phone: username=%s", user.getUsername());
            } catch (Exception e) {
                // гонка/дубликат имени — пробуем ещё раз найти
                user = findByPhone(kc.users().searchForUserByUserAttributeStream(realm, ATTR_PHONE, phone));
                if (user == null) {
                    log.error("cannot create user for phone", e);
                    challengePhoneForm(ctx, "Не удалось создать пользователя. Попробуйте позже.");
                    return false;
                }
            }
        }
        if (!user.isEnabled()) {
            challengePhoneForm(ctx, "Учётная запись отключена.");
            return false;
        }
        ctx.setUser(user);
        ctx.success();
        return true;
    }

    private static UserModel findByPhone(Stream<UserModel> stream) {
        return stream.findFirst().orElse(null);
    }

    private void challengePhoneForm(AuthenticationFlowContext ctx, String error) {
        LoginFormsProvider form = ctx.form();
        if (error != null) form.setError(error);
        ctx.challenge(form.createForm("phone-phone-form.ftl"));
    }

    private void challengeCallPage(AuthenticationFlowContext ctx, AuthenticationSessionModel session) {
        long ttlLeft = ttlLeft(session);
        LoginFormsProvider form = ctx.form()
                .setAttribute("callNumber", formatCallNumber(session.getAuthNote(NOTE_CALL)))
                .setAttribute("phoneMasked", maskPhone(session.getAuthNote(NOTE_PHONE)))
                .setAttribute("ttlLeft", ttlLeft);
        ctx.challenge(form.createForm("phone-call-form.ftl"));
    }

    private long ttlLeft(AuthenticationSessionModel session) {
        String started = session.getAuthNote(NOTE_STARTED);
        long ttl;
        try {
            ttl = Long.parseLong(session.getAuthNote(NOTE_TTL));
        } catch (NumberFormatException e) {
            ttl = 180;
        }
        if (started != null) {
            long elapsed = System.currentTimeMillis() / 1000 - Long.parseLong(started);
            return Math.max(0, ttl - elapsed);
        }
        return ttl;
    }

    private void clearNotes(AuthenticationSessionModel session) {
        session.removeAuthNote(NOTE_ID);
        session.removeAuthNote(NOTE_PHONE);
        session.removeAuthNote(NOTE_CALL);
        session.removeAuthNote(NOTE_STARTED);
        session.removeAuthNote(NOTE_TTL);
    }

    private VerificationClient client(AuthenticationFlowContext ctx) {
        String url = cfg(ctx, "verificationServiceUrl", "http://app:8000");
        String key = cfg(ctx, "serviceApiKey", "");
        return new VerificationClient(url, key);
    }

    private static String cfg(AuthenticationFlowContext ctx, String key, String def) {
        var config = ctx.getAuthenticatorConfig();
        if (config == null || config.getConfig() == null) return def;
        String v = config.getConfig().get(key);
        return (v == null || v.isBlank()) ? def : v;
    }

    /** 7XXXXXXXXXX | 8XXXXXXXXXX | +7... | 10 цифр → 7XXXXXXXXXX; null если не похоже. */
    static String normalizePhone(String raw) {
        if (raw == null) return null;
        String digits = raw.replaceAll("[^0-9]", "");
        if (digits.length() == 10) digits = "7" + digits;
        if (digits.length() == 11 && digits.startsWith("8")) digits = "7" + digits.substring(1);
        if (digits.length() != 11 || !digits.startsWith("7")) return null;
        try {
            long v = Long.parseLong(digits);
            // грубая проверка кодов: 7 + 3-значный код оператора + 7 цифр
            if (v < 70000000000L) return null;
            return digits;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    static String maskPhone(String phone) {
        if (phone == null || phone.length() < 10) return phone;
        return phone.substring(0, 2) + "*******" + phone.substring(phone.length() - 2);
    }

    /** 79395555028 → +7 (939) 555-50-28 */
    static String formatCallNumber(String digits) {
        if (digits == null) return "";
        String d = digits.replaceAll("[^0-9]", "");
        if (d.length() == 11 && d.startsWith("7")) {
            return String.format("+7 (%s) %s-%s-%s",
                    d.substring(1, 4), d.substring(4, 7), d.substring(7, 9), d.substring(9, 11));
        }
        return d;
    }
}
