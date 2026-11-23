package ru.merchvibe.keycloak;

import org.keycloak.authentication.Authenticator;
import org.keycloak.authentication.AuthenticatorFactory;
import org.keycloak.Config;
import org.keycloak.models.AuthenticationExecutionModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;

import java.util.List;

/**
 * Фабрика аутентификатора «Вход по звонку (обратный Flash Call)».
 *
 * Конфигурация (задаётся в админке при добавлении execution в flow):
 *   verificationServiceUrl — базовый URL сервиса верификации (http://app:8000)
 *   serviceApiKey          — X-Api-Key для /api/verify/*
 *   userMode               — create (найти или создать пользователя) | require (только найти)
 */
public class PhoneCallAuthenticatorFactory implements AuthenticatorFactory {

    public static final String ID = "phone-call-auth";

    private static final PhoneCallAuthenticator SINGLETON = new PhoneCallAuthenticator();

    @Override
    public Authenticator create(KeycloakSession session) {
        return SINGLETON;
    }

    @Override
    public void init(Config.Scope config) { }

    @Override
    public void postInit(KeycloakSessionFactory factory) { }

    @Override
    public void close() { }

    @Override
    public String getId() {
        return ID;
    }

    @Override
    public String getReferenceCategory() {
        return null;
    }

    @Override
    public boolean isConfigurable() {
        return true;
    }

    @Override
    public AuthenticationExecutionModel.Requirement[] getRequirementChoices() {
        return new AuthenticationExecutionModel.Requirement[]{
                AuthenticationExecutionModel.Requirement.REQUIRED,
                AuthenticationExecutionModel.Requirement.ALTERNATIVE,
                AuthenticationExecutionModel.Requirement.DISABLED
        };
    }

    @Override
    public boolean isUserSetupAllowed() {
        return false;
    }

    @Override
    public String getHelpText() {
        return "Проверка телефона звонком (обратный Flash Call Plusofon). " +
               "Пользователь вводит номер, звонит на показанный номер, вебхук подтверждает. " +
               "Пользователь ищется по атрибуту phoneNumber (или создаётся).";
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return List.of(
                new ProviderConfigProperty("verificationServiceUrl", "Verification service URL",
                        "Базовый URL сервиса верификации (FastAPI). Из сети docker: http://app:8000",
                        ProviderConfigProperty.STRING_TYPE, "http://app:8000"),
                new ProviderConfigProperty("serviceApiKey", "Service API key",
                        "X-Api-Key для запросов /api/verify/* (совпадает с API_KEY из .env стенда)",
                        ProviderConfigProperty.STRING_TYPE, ""),
                new ProviderConfigProperty("userMode", "User mode",
                        "create — создать пользователя при первом входе; require — пускать только существующих",
                        ProviderConfigProperty.LIST_TYPE, "create", "create", "require")
        );
    }

    @Override
    public String getDisplayType() {
        return "Phone Call Verification (Plusofon)";
    }
}
