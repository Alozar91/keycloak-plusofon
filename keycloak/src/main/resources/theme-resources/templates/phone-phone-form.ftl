<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=true; section>
    <#if section = "header">
        Вход по номеру телефона
    <#elseif section = "form">
        <p>Введите номер телефона — мы позвоним вам и подтвердим вход звонком.</p>
        <form id="kc-phone-form" action="${url.loginAction}" method="post">
            <div class="${properties.kcFormGroupClass!}">
                <label for="phone" class="${properties.kcLabelClass!}">Номер телефона</label>
                <input id="phone" name="phone" type="tel" required autofocus
                       placeholder="+7 999 000-00-00" autocomplete="off"
                       class="${properties.kcInputClass!}" />
            </div>
            <div class="${properties.kcFormGroupClass!}">
                <input type="submit" value="Продолжить"
                       class="${properties.kcButtonClass!} ${properties.kcButtonPrimaryClass!} ${properties.kcButtonLargeClass!}" />
            </div>
        </form>
    </#if>
</@layout.registrationLayout>
