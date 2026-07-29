import sys
import os
import getpass
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

def get_key_from_password(password: str, salt: bytes) -> bytes:
    """Derives a secure, 32-byte key from a small password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def encrypt_file(input_filename: str, output_filename: str, password: str):
    salt = os.urandom(16)
    key = get_key_from_password(password, salt)
    fernet = Fernet(key)
    
    with open(input_filename, 'rb') as f:
        file_data = f.read()
        
    encrypted_data = fernet.encrypt(file_data)
    
    with open(output_filename, 'wb') as f:
        f.write(salt + encrypted_data)
        
    print(f"[*] Success! Encrypted file saved as: {output_filename}")

def decrypt_file(input_filename: str, output_filename: str, password: str):
    with open(input_filename, 'rb') as f:
        file_content = f.read()
        
    salt = file_content[:16]
    encrypted_data = file_content[16:]
    
    key = get_key_from_password(password, salt)
    fernet = Fernet(key)
    
    try:
        decrypted_data = fernet.decrypt(encrypted_data)
    except Exception:
        print("[!] Decryption failed. Incorrect password or corrupted file.")
        sys.exit(1)
        
    with open(output_filename, 'wb') as f:
        f.write(decrypted_data)
        
    print(f"[*] Success! Decrypted file saved as: {output_filename}")

def main():
    # Ensure a file was passed as an argument
    if len(sys.argv) != 2:
        print("Usage: python crypt.py <filename>")
        sys.exit(1)

    input_file = sys.argv[1]

    # Ensure the file actually exists
    if not os.path.exists(input_file):
        print(f"[!] Error: File '{input_file}' does not exist.")
        sys.exit(1)

    # Determine mode based on file extension
    if input_file.endswith(".enc"):
        # === DECRYPTION MODE ===
        output_file = input_file[:-4]  # Remove the '.enc' extension
        print(f"Mode: DECRYPT ('{input_file}' -> '{output_file}')")
        
        password = getpass.getpass("Enter password: ")
        decrypt_file(input_file, output_file, password)
        
    else:
        # === ENCRYPTION MODE ===
        output_file = input_file + ".enc"
        print(f"Mode: ENCRYPT ('{input_file}' -> '{output_file}')")
        
        password = getpass.getpass("Enter password to encrypt: ")
        confirm_password = getpass.getpass("Confirm password: ")
        
        if password != confirm_password:
            print("[!] Passwords do not match. Aborting.")
            sys.exit(1)
            
        encrypt_file(input_file, output_file, password)

if __name__ == "__main__":
    main()