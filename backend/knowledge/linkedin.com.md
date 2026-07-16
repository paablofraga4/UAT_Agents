# LinkedIn — playbook

LinkedIn is a heavy SPA: sections load asynchronously, so after navigating,
insert a short `wait` (1000–2000ms) and re-observe (`extract_context`) before
acting. The UI language follows the user's account — for this user it is
**Spanish**, so use Spanish labels.

## First thing on entering
- A **cookie banner** often appears ("Aceptar" / "Rechazar" / "Aceptar cookies").
  Dismiss it BEFORE anything else — click "Rechazar" (or "Aceptar" if no reject),
  or press Escape. Nothing else is reliably clickable while it's up.
- A Google "One Tap" / "Iniciar sesión con Google" popup may overlay the page.
  Close it (its ✕ or Escape) before continuing.

## Primary navigation (top bar, Spanish)
- **Inicio** — the feed.
- **Mi red** — your network: pending invitations and "Personas que quizá
  conozcas" (people you may know) with **Conectar** buttons.
- **Empleos** — jobs.
- **Mensajes** — direct messaging (usually bottom-right or in the top bar).
- **Notificaciones** — notifications.
- **Yo** — your profile / account menu.

Prefer clicking the numbered nav entry from the observation (or `click_element`)
over guessing. Do NOT use English labels ("My Network", "Messaging") — they are
not on this UI.

## Messaging someone (very common task)
The conversation you want is almost never visible in the first observation — the
list is long and virtually scrolled. DO NOT click a person's name that isn't in
the current observation. Instead:
1. Open **Mensajes**.
2. Find the search box **"Buscar mensajes"** and `type_text` the person's name
   into it.
3. `wait` ~1000ms and `extract_context`. The matching conversation(s) now appear.
4. `click_text` (or `click_element`) the person's name in the results.
5. The composer is a **contenteditable** labelled **"Escribe un mensaje"** — use
   `type_text` (not fill). After typing, send with `press_key Enter` or click the
   **"Enviar"** button (it only enables once the composer registers input).
6. Confirm success by seeing the sent message appear in the thread.

If "Buscar mensajes" isn't present, the messaging panel hasn't rendered yet —
wait and re-observe, or click **Mensajes** again.

## Connecting with people
1. Go to **Mi red**.
2. Look for the **"Personas que quizá conozcas"** section (scroll down to it).
3. Each card has a **Conectar** button. Click it; the button flips to
   **Pendiente** — that IS success, move to the next card. (Repeated Conectar
   clicks on a changing list are legitimate progress, not a loop.)
4. Some cards open a "¿Quieres añadir una nota?" dialog — click **"Enviar sin
   nota"** / **"Enviar ahora"** to finish, or Escape to skip.

## Gotchas
- The feed has a "publicaciones nuevas" refresh pill — it is NOT the
  Notificaciones/section navigation; don't confuse it with nav.
- Profile and search results are also virtualised — scroll + re-observe to reach
  items below the fold rather than concluding they're absent.
