import streamlit as st
import json
import os
import uuid
import base64
import secrets
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from streamlit_autorefresh import st_autorefresh

VAULT_FILE = "vault.json"

def add_log(message):
    if "log_messages" not in st.session_state:
        st.session_state.log_messages = []
    st.session_state.log_messages.append(message)

def generate_aes_key_iv():
    key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    return key, iv

def aes_encrypt(key, iv, data: bytes):
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded_data = padder.update(data) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(padded_data) + encryptor.finalize()

def aes_decrypt(key, iv, data: bytes):
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()

class VaultEntry:
    def __init__(self, id, wrapped_dek, wrapping_iv, data_cipher, data_iv):
        self.id = id
        self.wrapped_dek = wrapped_dek
        self.wrapping_iv = wrapping_iv
        self.data_cipher = data_cipher
        self.data_iv = data_iv

    def as_dict(self):
        return {
            "id": self.id,
            "wrapped_dek": base64.b64encode(self.wrapped_dek).decode(),
            "wrapping_iv": base64.b64encode(self.wrapping_iv).decode(),
            "data_cipher": base64.b64encode(self.data_cipher).decode(),
            "data_iv": base64.b64encode(self.data_iv).decode(),
        }

    @staticmethod
    def from_dict(d):
        return VaultEntry(
            d["id"],
            base64.b64decode(d["wrapped_dek"]),
            base64.b64decode(d["wrapping_iv"]),
            base64.b64decode(d["data_cipher"]),
            base64.b64decode(d["data_iv"])
        )

class Vault:
    def __init__(self):
        self.entries = []
        self.kek, self.kek_iv = None, None
        self.load_vault()
        if self.kek is None or self.kek_iv is None:
            add_log("No KEK found. Generating new KEK and IV...")
            self.kek, self.kek_iv = generate_aes_key_iv()
            add_log("KEK and IV initialized.")

    def encrypt_data(self, text):
        dek, dek_iv = generate_aes_key_iv()
        add_log("Generated new DEK and IV for data.")
        data_cipher = aes_encrypt(dek, dek_iv, text.encode("utf-8"))
        add_log("Data encrypted with DEK.")
        wrapped_dek = aes_encrypt(self.kek, self.kek_iv, dek)
        add_log("DEK encrypted (wrapped) with current KEK.")
        entry = VaultEntry(str(uuid.uuid4()), wrapped_dek, self.kek_iv, data_cipher, dek_iv)
        self.entries.append(entry)
        self.save_vault()
        add_log(f"Encrypted entry stored with ID: {entry.id}")
        return entry.id

    def decrypt_data(self, entry_id):
        entry = next((e for e in self.entries if e.id == entry_id), None)
        if entry is None:
            add_log(f"Entry ID not found: {entry_id}")
            return "Entry not found"
        dek = aes_decrypt(self.kek, entry.wrapping_iv, entry.wrapped_dek)
        add_log("Unwrapped DEK using current KEK.")
        try:
            plaintext = aes_decrypt(dek, entry.data_iv, entry.data_cipher)
            add_log("Data decrypted with unwrapped DEK.")
            return plaintext.decode("utf-8")
        except Exception:
            add_log("Decryption failed. Likely wrong KEK/IV.")
            return "Decryption failed (wrong KEK?)"

    def rotate_kek(self):
        old_kek, old_iv = self.kek, self.kek_iv
        self.kek, self.kek_iv = generate_aes_key_iv()
        add_log("Generated new KEK and IV for rotation.")
        for entry in self.entries:
            dek = aes_decrypt(old_kek, entry.wrapping_iv, entry.wrapped_dek)
            add_log(f"Unwrapped DEK for entry {entry.id}.")
            entry.wrapped_dek = aes_encrypt(self.kek, self.kek_iv, dek)
            entry.wrapping_iv = self.kek_iv
            add_log(f"Rewrapped DEK for entry {entry.id} with new KEK.")
        self.save_vault()
        add_log("Rotated KEK and updated all entries.")

    def save_vault(self):
        vault_data = {
            "kek": base64.b64encode(self.kek).decode(),
            "kek_iv": base64.b64encode(self.kek_iv).decode(),
            "entries": [e.as_dict() for e in self.entries]
        }
        with open(VAULT_FILE, "w") as f:
            json.dump(vault_data, f, indent=2)
        add_log("Vault saved to file.")

    def load_vault(self):
        if not os.path.exists(VAULT_FILE):
            self.kek, self.kek_iv = None, None
            self.entries = []
            add_log("No vault file found, initializing new vault.")
            return
        with open(VAULT_FILE) as f:
            data = json.load(f)
            self.kek = base64.b64decode(data["kek"])
            self.kek_iv = base64.b64decode(data["kek_iv"])
            self.entries = [VaultEntry.from_dict(e) for e in data["entries"]]
            add_log(f"Vault loaded. {len(self.entries)} entries found.")

    def list_entries(self):
        return [e.id for e in self.entries]

st.set_page_config(page_title="AES KEK Rotation Vault Demo", layout="wide")
st.title("🔐 AES KEK Rotation Vault Demo")

if "vault_instance" not in st.session_state:
    st.session_state.vault_instance = Vault()
vault = st.session_state.vault_instance

option = st.sidebar.selectbox(
    "Choose an option",
    ("Encrypt data", "List entries", "Decrypt data", "Rotate KEK")
)

col1, col2, col3 = st.columns([2,1,1])

with col1:
    if option == "Encrypt data":
        text = st.text_area("Enter data to encrypt:", key="encrypt_box")
        if st.button("Encrypt"):
            if text:
                entry_id = vault.encrypt_data(text)
                st.success(f"Data encrypted. Entry ID: {entry_id}")
            else:
                st.warning("Text box is empty.")

    elif option == "List entries":
        ids = vault.list_entries()
        st.write("## Vault Entries")
        st.write(ids if ids else "No entries.")

    elif option == "Decrypt data":
        ids = vault.list_entries()
        if ids:
            entry_id = st.selectbox("Select Entry ID", ids)
            if st.button("Decrypt"):
                result = vault.decrypt_data(entry_id)
                st.info(f"Decrypted: {result}")
        else:
            st.warning("No entries to decrypt.")

    elif option == "Rotate KEK":
        if st.button("Rotate KEK"):
            vault.rotate_kek()
            st.success("KEK rotated and all entries updated.")

with col2:
    st.markdown("<h4 style='margin-bottom: 2px;'>📝 What's Happening</h4>", unsafe_allow_html=True)
    if st.button("Refresh Logs"):
        pass  # Just triggers rerun, refreshing logs display
    logs = st.session_state.get("log_messages", [])
    log_html = "<div style='background:#222;color:#eee;padding:10px;height:420px;overflow:auto;border-radius:4px;font-size:13px'>"
    log_html += "<br>".join(logs[-30:])
    log_html += "</div>"
    st.markdown(log_html, unsafe_allow_html=True)

with col3:
    st.markdown("<h4 style='margin-bottom: 2px;'>📁 Vault File Monitor</h4>", unsafe_allow_html=True)
    st_autorefresh(interval=3000, key="vault_file_monitor_refresh")
    if os.path.exists(VAULT_FILE):
        try:
            with open(VAULT_FILE, "r") as f:
                vault_json = json.load(f)
            st.json(vault_json, expanded=2)
        except Exception as e:
            st.error(f"Error reading vault file: {e}")
    else:
        st.info("No vault file present yet.")
