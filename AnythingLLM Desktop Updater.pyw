# -*- coding: utf-8 -*-
"""
AnythingLLM Desktop Updater
===========================

Programme graphique (fenetres pop-up) pour mettre a jour proprement
l'application "AnythingLLM Desktop" sous Windows.

Pourquoi il existe
------------------
Le bouton "Update" d'AnythingLLM ouvre toujours la MEME url :
    https://cdn.anythingllm.com/latest/AnythingLLMDesktop.exe
Le nom de fichier ne change jamais -> le navigateur (ou le cache du CDN)
ressert souvent l'ancien installeur, et on reinstalle la meme version.
Si l'app a ete installee "pour tous les utilisateurs", l'installeur per-user
affiche aussi "application deja installee".

Ce programme :
  - Detecte la version installee (registre Windows).
  - Telecharge le DERNIER installeur en contournant tout cache.
  - Lit et compare les numeros de version.
  - Ferme l'app, desinstalle proprement l'ancienne version si besoin,
    puis installe la derniere version en "utilisateur actuel".
  - Tes donnees (espaces de travail, reglages) sont TOUJOURS conservees.

Utilisation
-----------
Double-clique simplement sur ce fichier. Aucune commande a taper.
(Le fichier est en .pyw : il s'ouvre sans fenetre noire de terminal.)

Aucune dependance externe : uniquement la bibliotheque standard Python
(tkinter est inclus avec Python sous Windows).
"""

import os
import platform
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.request

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# ----------------------------------------------------------------------------
# Identite de l'application (nom transparent, bien visible partout)
# ----------------------------------------------------------------------------

APP_DISPLAY_NAME = "AnythingLLM Desktop"
WINDOW_TITLE = "AnythingLLM Desktop - Mise a jour"

# ----------------------------------------------------------------------------
# Constantes techniques
# ----------------------------------------------------------------------------

CDN_X64 = "https://cdn.anythingllm.com/latest/AnythingLLMDesktop.exe"
CDN_ARM64 = "https://cdn.anythingllm.com/latest/AnythingLLMDesktop-Arm64.exe"

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
APPDATA = os.environ.get("APPDATA", "")
INSTALL_GUESS = os.path.join(LOCALAPPDATA, "Programs", "AnythingLLM")
DATA_DIR = os.path.join(APPDATA, "anythingllm-desktop", "storage")

PROC_NAMES = ["AnythingLLM.exe", "anythingllm-desktop.exe"]

# Empeche l'ouverture de fenetres de terminal pour les sous-processus.
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


# ============================================================================
# Logique metier (sans interface) - identique a la version console
# ============================================================================

def pick_installer_url():
    machine = platform.machine().lower()
    if "arm" in machine:
        return CDN_ARM64, "ARM64"
    return CDN_X64, "x64"


def _reg_get(key, value_name):
    import winreg
    try:
        val, _ = winreg.QueryValueEx(key, value_name)
        return val
    except OSError:
        return None


def find_installations():
    """Retourne la liste des installs AnythingLLM trouvees dans le registre."""
    import winreg

    found = []
    roots = [
        (winreg.HKEY_CURRENT_USER,
         r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "utilisateur", 0),
        (winreg.HKEY_LOCAL_MACHINE,
         r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "tous les utilisateurs",
         winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE,
         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
         "tous les utilisateurs", 0),
    ]

    for hive, subkey, scope, extra_flag in roots:
        try:
            access = winreg.KEY_READ | extra_flag
            base = winreg.OpenKey(hive, subkey, 0, access)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    k = winreg.OpenKey(base, name, 0, access)
                except OSError:
                    continue
                try:
                    display = _reg_get(k, "DisplayName")
                    if not display or "anythingllm" not in display.lower():
                        continue
                    found.append({
                        "display_name": display,
                        "version": _reg_get(k, "DisplayVersion") or "?",
                        "install_location": _reg_get(k, "InstallLocation") or "",
                        "uninstall_string": _reg_get(k, "QuietUninstallString")
                                            or _reg_get(k, "UninstallString") or "",
                        "scope": scope,
                    })
                finally:
                    k.Close()
        finally:
            base.Close()
    return found


def exe_product_version(path):
    if not os.path.isfile(path):
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item -LiteralPath '{path}').VersionInfo.ProductVersion"],
            capture_output=True, text=True, timeout=30,
            creationflags=NO_WINDOW,
        )
        v = (out.stdout or "").strip()
        return v or None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Comparaison de versions (semantique) + lecture de la VRAIE version installee
# ----------------------------------------------------------------------------

def normalize_version(v):
    """'1.12.1.0' -> '1.12.1' ; enleve un eventuel prefixe 'v' et les espaces."""
    if not v:
        return None
    v = v.strip().lstrip("vV")
    parts = v.split(".")
    # On retire les '.0' superflus en fin (1.12.1.0 == 1.12.1).
    while len(parts) > 1 and parts[-1] in ("0", ""):
        parts = parts[:-1]
    return ".".join(parts)


def version_tuple(v):
    out = []
    for p in (normalize_version(v) or "0").split("."):
        digits = "".join(c for c in p if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(candidate, current):
    """True si 'candidate' est strictement plus recente que 'current'."""
    if not candidate or not current:
        return False
    return version_tuple(candidate) > version_tuple(current)


def install_dir_of(installation):
    """Deduit le dossier d'installation d'une entree de registre.
    L'install_location est parfois vide : on le reconstruit a partir du
    chemin du desinstalleur, sinon on retombe sur l'emplacement par defaut."""
    loc = (installation or {}).get("install_location") or ""
    if loc and os.path.isdir(loc):
        return loc
    cmd = (installation or {}).get("uninstall_string") or ""
    # Le chemin du desinstalleur est entre guillemets au debut de la commande.
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 1:
            unins = cmd[1:end]
            d = os.path.dirname(unins)
            if os.path.isdir(d):
                return d
    return INSTALL_GUESS if os.path.isdir(INSTALL_GUESS) else ""


def real_installed_version(installations):
    """Lit la VRAIE version de l'app depuis AnythingLLM.exe sur le disque.

    C'est l'information fiable : le registre peut indiquer une version
    differente (ex. 1.13.0) a cause d'une installation precedente
    interrompue, alors que les fichiers reels sont en 1.12.1. C'est cette
    incoherence qui fait croire a l'installeur que l'app est 'deja installee'.

    Retourne (version_reelle, dossier_install) ou (None, '') si introuvable.
    """
    # On essaie chaque emplacement connu, plus l'emplacement par defaut.
    candidate_dirs = []
    for it in (installations or []):
        d = install_dir_of(it)
        if d and d not in candidate_dirs:
            candidate_dirs.append(d)
    if INSTALL_GUESS not in candidate_dirs:
        candidate_dirs.append(INSTALL_GUESS)

    for d in candidate_dirs:
        exe = os.path.join(d, "AnythingLLM.exe")
        v = exe_product_version(exe)
        if v:
            return normalize_version(v), d
    return None, ""


def kill_running():
    closed_any = False
    for proc in PROC_NAMES:
        try:
            res = subprocess.run(["taskkill", "/IM", proc, "/F", "/T"],
                                 capture_output=True, text=True,
                                 creationflags=NO_WINDOW)
            if res.returncode == 0:
                closed_any = True
        except Exception:
            pass
    if closed_any:
        time.sleep(1.5)
    return closed_any


def download_installer(url, dest, progress_cb=None):
    bust = str(int(time.time()))
    sep = "&" if "?" in url else "?"
    url_nocache = f"{url}{sep}_={bust}"

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "User-Agent": "AnythingLLM-Desktop-Updater/1.0 (+python)",
    }
    req = urllib.request.Request(url_nocache, headers=headers)
    ctx = ssl.create_default_context()

    def _do(context):
        with urllib.request.urlopen(req, context=context, timeout=120) as resp:
            total = resp.length or 0
            downloaded = 0
            chunk = 1024 * 256
            with open(dest, "wb") as f:
                while True:
                    data = resp.read(chunk)
                    if not data:
                        break
                    f.write(data)
                    downloaded += len(data)
                    if progress_cb:
                        progress_cb(downloaded, total)
            return downloaded

    try:
        size = _do(ctx)
    except ssl.SSLError:
        size = _do(ssl._create_unverified_context())

    if size < 1024 * 1024:
        raise RuntimeError(
            f"Fichier telecharge trop petit ({size} octets). "
            "Le lien a peut-etre echoue ou renvoye une page d'erreur.")
    return dest


def uninstall(installation):
    cmd = installation.get("uninstall_string", "")
    if not cmd:
        loc = installation.get("install_location") or INSTALL_GUESS
        cand = os.path.join(loc, "Uninstall AnythingLLM.exe")
        if os.path.isfile(cand):
            cmd = f'"{cand}"'
    if not cmd:
        return False
    full = cmd if "/S" in cmd.upper() else f'{cmd} /S'
    try:
        subprocess.run(full, shell=True, timeout=300, creationflags=NO_WINDOW)
        time.sleep(3)
        return True
    except Exception:
        return False


def run_installer(path, silent=False):
    args = [path]
    if silent:
        args = [path, "/S", "--current-user"]
    subprocess.Popen(args)


# ============================================================================
# Interface graphique (tkinter)
# ============================================================================

class UpdaterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry("640x460")
        self.minsize(560, 420)
        self.configure(bg="#f3f3f3")

        # Etat partage entre les threads et l'interface.
        self.installs = []
        self.installed_version = None   # VRAIE version lue sur le disque
        self.registry_version = None    # version annoncee par le registre
        self.downloaded_path = None
        self.downloaded_version = None
        self.busy = False

        self._build_ui()
        # Detection initiale au demarrage (rapide, pas de telechargement).
        self.after(200, self._initial_scan)

    # ---- Construction de l'interface ----
    def _build_ui(self):
        header = tk.Frame(self, bg="#1f6feb", height=64)
        header.pack(fill="x")
        tk.Label(header, text=APP_DISPLAY_NAME, bg="#1f6feb", fg="white",
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=16, pady=8)
        tk.Label(header, text="Assistant de mise a jour", bg="#1f6feb",
                 fg="#dbe9ff", font=("Segoe UI", 10)).pack(side="left", pady=8)

        body = tk.Frame(self, bg="#f3f3f3")
        body.pack(fill="both", expand=True, padx=16, pady=12)

        self.status_var = tk.StringVar(value="Pret.")
        tk.Label(body, textvariable=self.status_var, bg="#f3f3f3",
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")

        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 8))

        self.log = scrolledtext.ScrolledText(body, height=12, wrap="word",
                                             font=("Consolas", 9), state="disabled",
                                             bg="white", relief="solid", borderwidth=1)
        self.log.pack(fill="both", expand=True)

        btns = tk.Frame(self, bg="#f3f3f3")
        btns.pack(fill="x", padx=16, pady=(0, 14))

        self.check_btn = ttk.Button(btns, text="Verifier les mises a jour",
                                    command=self.on_check)
        self.check_btn.pack(side="left")

        self.update_btn = ttk.Button(btns, text="Installer la mise a jour",
                                     command=self.on_update, state="disabled")
        self.update_btn.pack(side="left", padx=8)

        ttk.Button(btns, text="Quitter", command=self.destroy).pack(side="right")

    # ---- Helpers d'affichage (toujours appeles depuis le thread principal) ----
    def write(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_status(self, msg):
        self.status_var.set(msg)

    def set_progress(self, value):
        self.progress.configure(value=value)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.check_btn.configure(state=state)
        # Le bouton "Installer" n'est actif que si un telechargement a reussi.
        if busy:
            self.update_btn.configure(state="disabled")
        elif self.downloaded_path:
            self.update_btn.configure(state="normal")

    # ---- Scan initial ----
    def _initial_scan(self):
        self.installs = find_installations()

        # Version annoncee par le registre (peut etre fausse / en avance).
        # On garde la valeur BRUTE pour l'affichage (ex. '1.13.0').
        reg_versions = sorted({it["version"]
                               for it in self.installs if it.get("version")})
        self.registry_version = reg_versions[0] if reg_versions else None

        # VRAIE version, lue depuis AnythingLLM.exe sur le disque : c'est la
        # reference fiable pour decider si une mise a jour est disponible.
        self.installed_version, _install_dir = real_installed_version(self.installs)

        if self.installs or self.installed_version:
            self.write("Application detectee :")
            if self.installed_version:
                self.write(f"   Version reelle installee (sur le disque) : "
                           f"{self.installed_version}")
            for it in self.installs:
                self.write(f"   - {it['display_name']}  "
                           f"(registre : version {it['version']}, "
                           f"installee pour : {it['scope']})")
            # Detection de l'incoherence registre vs disque (= la cause du bug).
            if (self.installed_version and self.registry_version
                    and normalize_version(self.installed_version)
                    != normalize_version(self.registry_version)):
                self.write("")
                self.write("   /!\\ INCOHERENCE detectee :")
                self.write(f"       le registre dit {self.registry_version}, "
                           f"mais l'app reelle est {self.installed_version}.")
                self.write("       C'est ce qui fait croire a l'installeur que l'app "
                           "est 'deja installee'.")
                self.write("       Ce programme se base sur la VRAIE version "
                           f"({self.installed_version}) pour decider.")
            if any(it["scope"] == "tous les utilisateurs" for it in self.installs):
                self.write("   /!\\ Une installation 'pour tous les utilisateurs' est "
                           "presente : cela peut aussi bloquer la mise a jour.")
        else:
            self.write(f"Aucune installation de {APP_DISPLAY_NAME} detectee.")
        if os.path.isdir(DATA_DIR):
            self.write("Tes donnees (espaces de travail, reglages) sont presentes "
                       "et seront conservees.")
        self.write("")
        self.set_status("Clique sur 'Verifier les mises a jour'.")

    # ---- Bouton : Verifier ----
    def on_check(self):
        if self.busy:
            return
        if not messagebox.askyesno(
                WINDOW_TITLE,
                "Verifier maintenant la derniere version disponible de\n"
                f"{APP_DISPLAY_NAME} ?\n\n"
                "Cela telecharge l'installeur officiel (~150 Mo) mais "
                "n'installe rien sans ta confirmation."):
            return
        self.downloaded_path = None
        self.downloaded_version = None
        self.set_busy(True)
        self.set_progress(0)
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        try:
            url, arch = pick_installer_url()
            self._ui(self.set_status, f"Telechargement de l'installeur ({arch})...")
            self._ui(self.write, f"Telechargement depuis le CDN officiel ({arch})...")
            dest = os.path.join(tempfile.gettempdir(),
                                "AnythingLLMDesktop_latest.exe")

            def progress_cb(done, total):
                if total:
                    pct = done * 100 / total
                    self._ui(self.set_progress, pct)
                    self._ui(self.set_status,
                             f"Telechargement... {int(pct)}% "
                             f"({done // (1024*1024)} / {total // (1024*1024)} Mo)")

            download_installer(url, dest, progress_cb)
            self.downloaded_path = dest
            self._ui(self.set_progress, 100)

            version = normalize_version(exe_product_version(dest))
            self.downloaded_version = version
            self._ui(self.write, f"Derniere version disponible (CDN) : "
                                 f"{version or 'inconnue'}")

            self._ui(self._finish_check, version, self.installed_version)
        except Exception as e:
            self._ui(self.write, f"ERREUR : {e}")
            self._ui(self.set_status, "Echec du telechargement.")
            self._ui(lambda: messagebox.showerror(
                WINDOW_TITLE,
                f"Le telechargement a echoue :\n\n{e}\n\n"
                "Verifie ta connexion internet et reessaie."))
        finally:
            self._ui(self.set_busy, False)

    def _finish_check(self, version, installed):
        """Decide s'il y a une mise a jour en comparant la version du CDN a la
        VRAIE version installee (lue sur le disque), pas a celle du registre."""
        old = installed if installed else "aucune"

        if not version:
            self.write("=> Impossible de lire la version du fichier telecharge.")
            self.set_status("Version inconnue.")
            self.update_btn.configure(state="disabled")
            return

        # Cas 1 : aucune app installee -> on peut installer.
        # Cas 2 : la version du CDN est strictement plus recente -> mise a jour.
        if (not installed) or is_newer(version, installed):
            self.write(f"=> Mise a jour disponible : {old} -> {version}")
            self.set_status(f"Mise a jour disponible : {version}")
            self.update_btn.configure(state="normal")
            messagebox.showinfo(
                WINDOW_TITLE,
                f"Une nouvelle version de {APP_DISPLAY_NAME} est disponible :\n\n"
                f"   Version installee  : {old}\n"
                f"   Nouvelle version   : {version}\n\n"
                "Clique sur 'Installer la mise a jour' quand tu es pret.")
        elif version == installed:
            self.write("=> Tu as deja la derniere version publiee. Rien a installer.")
            self.set_status(f"A jour (version {version}).")
            self.update_btn.configure(state="disabled")
            messagebox.showinfo(
                WINDOW_TITLE,
                f"Tu as deja la derniere version disponible de\n"
                f"{APP_DISPLAY_NAME} (version {version}).\n\n"
                "Aucune mise a jour necessaire.")
        else:
            # Version installee plus recente que le CDN (rare) : on laisse le
            # choix, mais on ne pousse pas a "downgrader".
            self.write(f"=> Ta version installee ({installed}) est plus recente "
                       f"que celle du CDN ({version}). Rien a faire.")
            self.set_status(f"Version installee {installed} (CDN : {version}).")
            self.update_btn.configure(state="disabled")
            messagebox.showinfo(
                WINDOW_TITLE,
                f"Ta version installee ({installed}) est deja plus recente que "
                f"celle proposee par le CDN ({version}).\n\n"
                "Aucune mise a jour necessaire.")

    # ---- Bouton : Installer ----
    def on_update(self):
        if self.busy or not self.downloaded_path:
            return
        if not messagebox.askyesno(
                WINDOW_TITLE,
                f"Installer maintenant la mise a jour de {APP_DISPLAY_NAME} ?\n\n"
                "Le programme va :\n"
                "  1. Fermer l'application si elle est ouverte\n"
                "  2. Desinstaller proprement l'ancienne version\n"
                "  3. Installer la derniere version (utilisateur actuel)\n\n"
                "Tes espaces de travail et reglages seront CONSERVES."):
            return
        self.set_busy(True)
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            self._ui(self.set_status, "Fermeture de l'application...")
            self._ui(self.write, "Fermeture de AnythingLLM Desktop si ouverte...")
            kill_running()

            if self.installs:
                # On retire d'abord les installs 'tous les utilisateurs'
                # (cause frequente du blocage), puis les installs 'utilisateur'.
                self.installs.sort(
                    key=lambda it: 0 if it["scope"] == "tous les utilisateurs" else 1)
                for it in self.installs:
                    self._ui(self.set_status,
                             f"Desinstallation de la version {it['version']}...")
                    self._ui(self.write,
                             f"Desinstallation de {it['display_name']} "
                             f"(version {it['version']})...")
                    if uninstall(it):
                        self._ui(self.write, "   Ancienne version retiree.")
                    else:
                        self._ui(self.write,
                                 "   Desinstalleur introuvable : installation par-dessus.")

            self._ui(self.set_status, "Lancement de l'installeur...")
            self._ui(self.write, "Lancement de l'installeur de la nouvelle version...")
            run_installer(self.downloaded_path, silent=False)

            self._ui(self._finish_update)
        except Exception as e:
            self._ui(self.write, f"ERREUR : {e}")
            self._ui(lambda: messagebox.showerror(
                WINDOW_TITLE, f"La mise a jour a echoue :\n\n{e}"))
        finally:
            self._ui(self.set_busy, False)

    def _finish_update(self):
        self.set_status("Installeur lance.")
        self.write("")
        self.write("L'installeur officiel est ouvert. Suis les etapes a l'ecran "
                   "(choisis 'utilisateur actuel' si on te le demande).")
        self.write("ASTUCE : si l'installation ne demarre pas du premier coup "
                   "(fichiers encore verrouilles juste apres la desinstallation), "
                   "clique simplement sur 'Reessayer' / 'Retry' : la 2e tentative "
                   "fonctionne.")
        self.update_btn.configure(state="disabled")
        messagebox.showinfo(
            WINDOW_TITLE,
            "L'installeur de la nouvelle version a ete lance.\n\n"
            "Laisse-le aller jusqu'au bout, puis rouvre "
            f"{APP_DISPLAY_NAME}.\n\n"
            "ASTUCE : si rien ne se passe au premier essai, clique sur\n"
            "'Reessayer' / 'Retry' dans la fenetre de l'installeur.\n"
            "(Juste apres la desinstallation, certains fichiers peuvent\n"
            "etre encore verrouilles une seconde ; la 2e tentative passe.)\n\n"
            "Tes espaces de travail et reglages sont intacts.")

    # ---- Marshalling thread -> interface ----
    def _ui(self, func, *args):
        """Execute func(*args) dans le thread principal de tkinter."""
        self.after(0, lambda: func(*args))


def main():
    if os.name != "nt":
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(WINDOW_TITLE,
                             "Ce programme fonctionne uniquement sous Windows.")
        return
    app = UpdaterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
