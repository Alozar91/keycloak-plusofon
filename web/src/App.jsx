import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const SCREENS = { INPUT: "input", WAITING: "waiting", SUCCESS: "success" };

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

export default function App() {
  const [screen, setScreen] = useState(SCREENS.INPUT);
  const [phone, setPhone] = useState("");
  const [error, setError] = useState("");
  const [verification, setVerification] = useState(null); // {verification_id, call_number, ttl, mock}
  const [ttlLeft, setTtlLeft] = useState(0);
  const timerRef = useRef(null);
  const pollRef = useRef(null);

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
              Здесь тестовый стенд завершает работу. В целевой архитектуре на этом шаге
              Keycloak (SPI) завершит аутентификацию.
            </p>
            <button type="button" className="link" onClick={restart}>
              ← Проверить другой номер
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
