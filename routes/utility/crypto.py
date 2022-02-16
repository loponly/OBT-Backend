from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from typing import Union
from hashlib import blake2s
import os

class CipherAES:
    secret = blake2s(os.environ.get('SECURE_KEY', 'r8ccTC5kgYqMAsGLQQGptq').encode(), salt=b'\xa3^\x05\xc7\xc8\xa9\xf5\xe2').digest()

    @staticmethod
    def encrypt(message: bytes) -> dict:
        assert type(message) == bytes, "Only raw bytes are allowed to encrypted"
        nonce = get_random_bytes(16)
        ct, tag = AES.new(CipherAES.secret, AES.MODE_SIV, nonce=nonce).encrypt_and_digest(message)
        return nonce + tag + ct 

    @staticmethod
    def decrypt(data: dict) -> bytes:
        assert type(data) == bytes, "Only raw bytes are allowed to decrypted"
        return AES.new(CipherAES.secret, AES.MODE_SIV, nonce=data[:16]).decrypt_and_verify(data[32:], data[16:32])

