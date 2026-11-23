<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=false; section>
    <#if section = "header">
        Подтверждение по звонку
    <#elseif section = "form">
        <p>С телефона <b>${phoneMasked}</b> позвоните на номер и сбросьте после первого гудка:</p>
        <h2 style="font-size: 2em; text-align: center; margin: 16px 0;">${callNumber}</h2>
        <p>Ожидаем ваш звонок… <span id="kc-phone-ttl">${ttlLeft?c}</span> с</p>
        <form id="kc-phone-wait" action="${url.loginAction}" method="post"></form>
        <script>
            (function () {
                var ttl = ${ttlLeft?c};
                var ttlEl = document.getElementById('kc-phone-ttl');
                setInterval(function () {
                    if (ttl > 0) { ttl--; ttlEl.textContent = ttl; }
                }, 1000);
                setInterval(function () {
                    document.getElementById('kc-phone-wait').submit();
                }, 4000);
            })();
        </script>
    </#if>
</@layout.registrationLayout>
