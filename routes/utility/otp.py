import pyotp
from .crypto import CipherAES

class OTP:
    @staticmethod
    def generate_secret() -> bytes:
        return CipherAES.encrypt(pyotp.random_base32().encode())

    @classmethod
    def verify(cls, secret: bytes, otp_six_digits: str) -> bool:
        return pyotp.TOTP(cls.get_secret_str(secret)).verify(otp_six_digits)

    @classmethod
    def get_otp(cls, secret: bytes):
        return pyotp.TOTP(cls.get_secret_str(secret)).now()

    @staticmethod
    def get_secret_str(secret: bytes):
        return CipherAES.decrypt(secret).decode()
