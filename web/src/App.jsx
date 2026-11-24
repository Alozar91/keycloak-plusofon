import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { kc, profile } from "./kc.js";

const SCREENS = { INPUT: "input", WAITING: "waiting", SUCCESS: "success" };
const PROFILE_PATH = "/profile";

function formatPhoneInput(value) {
  // мягкое форматирование +7 (999) 123-45-67
  let d = value.replace(/\D/g, "");
  if (d.startsWith("8") && d.length === 11) d = "7" + d.slice(1);
  if (d.startsWith("9") && d.length === 10) d = "7" + d;
  if (!d.startsWith("7")) d = "7" + d;
  d = d.slice(0, 11);
  const p = d.slice(1);
  let out = "+7";
  if (p.length > 0) out += ` (${p.slice(0, 3)}`;
  if (p.length >= 3) out += ")";
  if (p.length > 3) out += ` ${p.slice(3, 6)}`;
  if (p.length > 6) out += `-${p.slice(6, 8)}`;
  if (p.length > 8) out += `-${p.slice(8, 10)}`;
  return out;
}

// --------------------------------------------------------------------- Профиль

function ProfilePage() {
  const [user, setUser] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  // формы
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mails, setMails] = useState([]);

  // привязка телефона: тот же флоу, что и вход
  const [phoneScreen, setPhoneScreen] = useState("idle"); // idle | input | waiting
  const [phone, setPhone] = useState("");
  const [verification, setVerification] = useState(null);
  const [ttlLeft, setTtlLeft] = useState(0);
  const pollRef = useRef(null);
  const timerRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const u = await profile.get();
      setUser(u);
      setUsername(u.username || "");
      setEmail(u.email || "");
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const stopTimers = () => {
    clearInterval(timerRef.current); clearInterval(pollRef.current);
  };

  // поллинг верификации для привязки телефона
  useEffect(() => {
    if (phoneScreen !== "waiting" || !verification) return;
    timerRef.current = setInterval(() => setTtlLeft((t) => (t > 0 ? t - 1 : 0)), 1000);
    pollRef.current = setInterval(async () => {
      try {
        const st = await api.status(verification.verification_id);
        if (st.status === "verified") {
          stopTimers();
          try {
            await profile.bindPhone(phone, verification.verification_id);
            setNotice("Телефон привязан");
            setPhoneScreen("idle");
            load();
          } catch (e) { setError(e.message); setPhoneScreen("idle"); }
        } else if (st.status === "expired") {
          stopTimers();
          setError("Время истекло, начните заново");
          setPhoneScreen("input");
        }
      } catch { /* игнорируем до следующего тика */ }
    }, 2000);
    return stopTimers;
  }, [phoneScreen, verification, phone, load]);

  const act = async (fn, okMsg) => {
    setBusy(true); setError(""); setNotice("");
    try {
      const r = await fn();
      setNotice(okMsg + (r && r.hint ? ` (${r.hint})` : ""));
      await load();
      // после смены email показать dev-outbox
      if (okMsg.includes("mail")) {
        const ob = await profile.outbox(email);
        setMails(ob.items || []);
      }
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const startPhoneBind = async () => {
    setError(""); setNotice("");
    try {
      const res = await api.start(phone);
      setVerification(res);
      setTtlLeft(res.ttl);
      setPhoneScreen("waiting");
    } catch (e) { setError("Не удалось запустить проверку: " + e.message); }
  };

  const simulateCall = async () => {
    try { await api.simulate(verification.verification_id); }
    catch (e) { setError("Симуляция не удалась: " + e.message); }
  };

  if (error && !user) {
    return (
      <div className="page"><div className="card">
        <h1>Профиль</h1>
        <p className="error">{error}</p>
        <button className="primary" onClick={() => kc.login()}>Войти заново</button>
      </div></div>
    );
  }

  if (!user) {
    return <div className="page"><div className="card"><div className="spinner" /></div></div>;
  }

  const mmss = `${String(Math.floor(ttlLeft / 60)).padStart(2, "0")}:${String(ttlLeft % 60).padStart(2, "0")}`;
  const telHref = verification
    ? `tel:+7${verification.call_number.replace(/\D/g, "").replace(/^8/, "")}`
    : "#";

  return (
    <div className="page">
      <div className="card wide">
        <div className="row head">
          <h1>Профиль</h1>
          <button className="link" onClick={kc.logout}>Выйти</button>
        </div>
        <p className="subtitle">{user.username} · вход через Keycloak</p>

        {notice && <p className="notice">{notice}</p>}
        {error && <p className="error">{error}</p>}

        <section>
          <h2>Способы входа</h2>
          <ul className="logins">
            <li>
              <span className={user.logins.phone ? "on" : "off"}>{user.logins.phone ? "✓" : "—"}</span>
              <b>Телефон (звонок)</b>
              <span className="muted">{user.phone || "не привязан"}</span>
              {user.logins.phone
                ? <button className="danger" disabled={busy} onClick={() => act(() => profile.unbindPhone(), "Телефон отвязан")}>Отвязать</button>
                : <button className="primary" onClick={() => { setPhoneScreen("input"); setError(""); }}>Привязать</button>}
            </li>
            <li>
              <span className={user.logins.password ? "on" : "off"}>{user.logins.password ? "✓" : "—"}</span>
              <b>Пароль</b>
              <span className="muted">{user.logins.password ? "задан" : "не задан"}</span>
            </li>
            <li>
              <span className={user.logins.email ? "on" : "off"}>{user.logins.email ? "✓" : "—"}</span>
              <b>Почта</b>
              <span className="muted">
                {user.email || "не задана"}{user.email && (user.email_verified ? " · подтверждена" : " · НЕ подтверждена")}
              </span>
            </li>
          </ul>
        </section>

        {phoneScreen === "input" && (
          <section>
            <h2>Привязка телефона</h2>
            <p className="hint">Подтвердите номер звонком — как при входе.</p>
            <label>Ваш номер</label>
            <input inputMode="tel" value={phone} placeholder="+7 (999) 123-45-67"
                   onChange={(e) => setPhone(formatPhoneInput(e.target.value))} autoFocus />
            <button className="primary" disabled={phone.replace(/\D/g, "").length < 11 || busy}
                    onClick={startPhoneBind}>Продолжить</button>
            <button className="link" onClick={() => setPhoneScreen("idle")}>Отмена</button>
          </section>
        )}

        {phoneScreen === "waiting" && verification && (
          <section>
            <h2>Привязка телефона</h2>
            <p className="lead">Позвоните с вашего номера на номер:</p>
            <a className="call-number" href={telHref}>{verification.call_number}</a>
            <p className="hint">Сбросьте после первого гудка. Осталось <b>{mmss}</b>.</p>
            <div className="spinner" />
            {verification.mock && (
              <button className="secondary" onClick={simulateCall}>⟲ Симулировать звонок (mock)</button>
            )}
          </section>
        )}

        <section>
          <h2>Логин</h2>
          <label>Username (для входа + пароль)</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} />
          <button className="primary" disabled={busy || username === user.username}
                  onClick={() => act(() => profile.setUsername(username), "Username обновлён")}>
            Сохранить username
          </button>
        </section>

        <section>
          <h2>Пароль</h2>
          <label>{user.logins.password ? "Новый пароль" : "Задать пароль (мин. 8 символов)"}</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="primary" disabled={busy || password.length < 8}
                  onClick={() => act(() => profile.setPassword(password), "Пароль сохранён").then(() => setPassword(""))}>
            {user.logins.password ? "Сменить пароль" : "Задать пароль"}
          </button>
        </section>

        <section>
          <h2>Почта</h2>
          <label>Email (подтверждение — письмом, ссылка в dev-outbox)</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="primary" disabled={busy || !email.includes("@")}
                  onClick={() => act(() => profile.setEmail(email), "Письмо отправлено")}>
            {user.logins.email ? "Сменить email" : "Добавить email"}
          </button>
          {mails.length > 0 && (
            <div className="outbox">
              <p className="hint">Письма (dev-режим):</p>
              {mails.map((m) => (
                <p key={m.id}>
                  {m.link ? <a href={m.link}>Подтвердить email →</a> : m.subject}
                </p>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------- Вход

function App() {
  const [screen, setScreen] = useState(SCREENS.INPUT);
  const [phone, setPhone] = useState("");
  const [error, setError] = useState("");
  const [verification, setVerification] = useState(null); // {verification_id, call_number, ttl, mock}
  const [ttlLeft, setTtlLeft] = useState(0);
  const timerRef = useRef(null);
  const pollRef = useRef(null);

  const isProfile = location.pathname === PROFILE_PATH;

  useEffect(() => {
    if (!isProfile) return;
    kc.handleRedirect()
      .then((logged) => { if (!logged && !kc.token) kc.login(); })
      .catch((e) => console.error(e));
  }, [isProfile]);

  const stopTimers = useCallback(() => {
    clearInterval(timerRef.current);
    clearInterval(pollRef.current);
    timerRef.current = null;
    pollRef.current = null;
  }, []);

  const startVerification = useCallback(async () => {
    setError("");
    try {
      const res = await api.start(phone);
      setVerification(res);
      setTtlLeft(res.ttl);
      setScreen(SCREENS.WAITING);
    } catch (e) {
      setError("Не удалось запустить проверку: " + e.message);
    }
  }, [phone]);

  // ожидание: обратный отсчёт + опрос статуса
  useEffect(() => {
    if (screen !== SCREENS.WAITING || !verification) return;

    timerRef.current = setInterval(() => {
      setTtlLeft((t) => (t > 0 ? t - 1 : 0));
    }, 1000);

    pollRef.current = setInterval(async () => {
      try {
        const st = await api.status(verification.verification_id);
        if (st.status === "verified") {
          stopTimers();
          setScreen(SCREENS.SUCCESS);
        } else if (st.status === "expired") {
          stopTimers();
          setError("Время истекло. Запросите новый номер для звонка.");
          setScreen(SCREENS.INPUT);
        }
      } catch {
        /* сетевые сбои опроса игнорируем до следующего тика */
      }
    }, 2000);

    return stopTimers;
  }, [screen, verification, stopTimers]);

  const simulateCall = useCallback(async () => {
    setError("");
    try {
      await api.simulate(verification.verification_id);
    } catch (e) {
      setError("Симуляция не удалась: " + e.message);
    }
  }, [verification]);

  const restart = useCallback(() => {
    stopTimers();
    setVerification(null);
    setError("");
    setScreen(SCREENS.INPUT);
  }, [stopTimers]);

  const mmss = `${String(Math.floor(ttlLeft / 60)).padStart(2, "0")}:${String(ttlLeft % 60).padStart(2, "0")}`;
  const telHref = verification
    ? `tel:+7${verification.call_number.replace(/\D/g, "").replace(/^8/, "")}`
    : "#";

  if (isProfile) return <ProfilePage />;

  return (
    <div className="page">
      <div className="card">
        <h1>Вход по звонку</h1>
        <p className="subtitle">Обратный Flash Call — тестовый стенд</p>

        {screen === SCREENS.INPUT && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              startVerification();
            }}
          >
            <label htmlFor="phone">Ваш номер телефона</label>
            <input
              id="phone"
              inputMode="tel"
              placeholder="+7 (999) 123-45-67"
              value={phone}
              onChange={(e) => setPhone(formatPhoneInput(e.target.value))}
              autoFocus
            />
            <button type="submit" className="primary" disabled={phone.replace(/\D/g, "").length < 11}>
              Продолжить
            </button>
            {error && <p className="error">{error}</p>}
            <p className="hint">
              Мы покажем номер, на который нужно позвонить с вашего телефона — сбросьте
              звонок после первого гудка.
            </p>
          </form>
        )}

        {screen === SCREENS.WAITING && verification && (
          <div>
            {verification.mock && <div className="badge">MOCK-режим: звонок имитируется</div>}
            <p className="lead">Позвоните с вашего номера на номер:</p>
            <a className="call-number" href={telHref}>
              {verification.call_number}
            </a>
            <p className="hint">
              Наберите номер со своего телефона и сбросьте после первого гудка.
              Проверка закончится через <b>{mmss}</b>.
            </p>
            <div className="spinner" aria-label="ожидание звонка" />
            {verification.mock && (
              <button type="button" className="secondary" onClick={simulateCall}>
                ⟲ Симулировать звонок (mock)
              </button>
            )}
            <button type="button" className="link" onClick={restart}>
              ← Другой номер
            </button>
            {error && <p className="error">{error}</p>}
          </div>
        )}

        {screen === SCREENS.SUCCESS && (
          <div className="success">
            <div className="check">✓</div>
            <p className="lead">Номер подтверждён!</p>
            <p className="hint">
              Пользователь создан в Keycloak. Откройте профиль, чтобы добавить
              логин с паролем и почту.
            </p>
            <a className="btn-link" href="/profile">Открыть профиль →</a>
            <button type="button" className="link" onClick={restart}>
              ← Проверить другой номер
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
