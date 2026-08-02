# COOKIES_SETUP.md — Desbloquear videos bot-checked (mixes/álbumes largos de reggae)

## Problema
`/api/play` devuelve 500 con **"Sign in to confirm you're not a bot"** para mixes largos
(por ejemplo `qRGDHM6Esqk` Music Brokers, `SUpRMcWin64` Reggae Vesta).
Los videos normales SÍ funcionan.

**Causa raíz (verificada):**
- YouTube aplica bot-check a esos mixes para IPs de datacenter (VPS).
- Las cookies actuales son **ANÓNIMAS** (sin sesión logueada) → no autentican.
- **NO existe vía técnica sin credenciales del usuario**: el OAuth está muerto en todo
  yt-dlp (Google lo deshabilitó, no es decisión de yt-dlp — probado hasta 2024.12.13),
  el cliente `tv_embedded` sin cookies también falla, y todos los clientes de yt-dlp
  fallan igual con cookies anónimas.

**La ÚNICA solución:** cookies de sesión REAL de navegador (que contengan `SID` y `HSID`).

> Además de las cookies, YouTube exige resolver el **n-challenge** para devolver
> formatos de audio. Se resuelve con el challenge solver **yt-dlp-ejs** (usa
> Deno, ya instalado). Ver "Instalar challenge solver" abajo. Sin él, yt-dlp
> solo obtiene storyboards (imágenes) → "Requested format is not available".

---

## Haz esto (5 minutos)

### 1. Instala la extensión en tu navegador
- Chrome / Edge / Brave: busca **"Get cookies.txt LOCALLY"** en la Chrome Web Store e instálala.
- Firefox: compatible via WebExtensions.

### 2. Inicia sesión en YouTube
- Abre https://www.youtube.com en una pestaña NUEVA y **Asegúrate de estar logueado**
  (tu foto de perfil en la esquina superior derecha).

### 3. Exporta las cookies
- Con YouTube abierto y logueado, haz clic en el **ícono de la extensión**.
- Elige **"Export"** (Netscape format / formato texto).
- El navegador descargará un archivo llamado algo como `cookies.txt`.

### 4. VERIFICA que tiene SID y HSID (imprescindible)
Abre el archivo descargado con un editor de texto y confirma que **al menos** estas
dos líneas existan (los valores son secretos, no los compartas):

```
.youtube.com	TRUE	/	TRUE	1800000000	SID	xxxxxxxx...
.youtube.com	TRUE	/	TRUE	1800000000	HSID	xxxxx...
```

> Si NO ves `SID` y `HSID`, **NO continúes**: repite el paso 2 (no estás logueado).
> Cookies sin SID/HSID = anónimas = no sirven para el bot-check.

### 5. Pégalas en el servidor
Sube ese archivo al servidor (ej: `/root/cookies_exportadas.txt`) y NORMALIZALO
(imprescindible, ver advertencia abajo):

```bash
cd /root/proyectos/Playme
# 1) filtra youtube/google + normaliza dominios (evita el bug de Python 3.12):
python3 backend/normalize_cookies.py /root/cookies_exportadas.txt
#    -> genera cookies.txt (solo youtube/google, listo para yt-dlp)

# 2) verificar que llegaron SID y HSID (debe imprimir >=1 para cada uno):
grep -c $'\tSID\t'   cookies.txt
grep -c $'\tHSID\t'  cookies.txt
```

> ⚠️ El archivo exportado crudo (con cookies de TODOS los sitios) ROMPE el
> parser de yt-dlp/Python 3.12 (`assert domain_specified == initial_dot`).
> Por eso se filtra y normaliza. También es más seguro: no subes cookies de
> otros sitios (Netflix, bancos, etc.) al servidor.

El resolver **ya usa `cookies.txt` automáticamente** vía una copia temporal
(`/tmp/playme_cookies.txt`), por lo que NO hace falta reiniciar nada.

---

## Instalar challenge solver (yt-dlp-ejs) — UNA VEZ

Sin este paquete, yt-dlp no resuelve el n-challenge de YouTube y no obtiene
formatos de audio (solo storyboards/imágenes).

```bash
pip install --break-system-packages yt-dlp-ejs
# verificar:
python3 -c "import yt_dlp_ejs; print('OK')"
```

yt-dlp lo detecta automáticamente (usa Deno como runtime JS). Requisito: Deno
instalado (`which deno`). Verificar con un video cualquiera:

```bash
yt-dlp --cookies /tmp/playme_cookies.txt --get-url --format '251/bestaudio' \
  'https://www.youtube.com/watch?v=SUpRMcWin64'
# debe imprimir una URL https://rr*.googlevideo.com/... (no un ERROR)
```

---

## ⚠️ ADVERTENCIA CRÍTICA (ya pasó 2 veces)
**NUNCA** ejecutes yt-dlp con `--cookies` apuntando DIRECTAMENTE a `cookies.txt` o
`cookies_master.txt`. yt-dlp los **REESCRIBE y los CORROMPE** (borra SID/HSID y rompe
la sesión).

El resolver de PlayMe ya está protegido: copia `cookies.txt` a `/tmp/playme_cookies.txt`
y pasa esa copia a yt-dlp. Si tú usas yt-dlp a mano, usa SIEMPRE la copia temp:
`--cookies /tmp/playme_cookies.txt`.

---

## Verificación final
Después de pegar las cookies, prueba un mix bloqueado:

```bash
# desde el navegador o con cualquier cliente HTTP:
curl -X POST http://<host>:8191/api/play -d '{"video_id":"SUpRMcWin64"}'
```

Deberías recibir un 200 (no 500 con "not a bot"). Si aún falla, vuelve a verificar
que `cookies.txt` tenga `SID` y `HSID` (pueden caducar si la sesión expira).

---

## Qué necesita el usuario (resumen)
1. Navegador con la extensión **"Get cookies.txt LOCALLY"**.
2. Estar **logueado** en youtube.com.
3. Exportar cookies a un archivo.
4. Verificar que contiene `SID` y `HSID`.
5. Copiarlo al servidor como `cookies.txt` (y opcional `cookies_master.txt`).

Nada más: el flujo de PlayMe ya está listo.
