import os
import json
from collections import namedtuple
from web3 import Web3
from web3 import Account
from web3.exceptions import TransactionNotFound
from web3.middleware import geth_poa_middleware
from result import Result, Ok, Err
from Crypto.Protocol.KDF import HKDF
from Crypto.Hash import BLAKE2b, SHA512
from pathlib import Path

from .crypto import CipherAES

node_url = os.environ.get('BSC_RPC', 'https://data-seed-prebsc-1-s1.binance.org:8545/')
KeyPair = namedtuple('KeyPair', ['pub', 'priv'])

class OBToken:
    symbol ="OBT"
    max_token = 10**(18 + 8)
    token_decimal = 10**18
    min_confirmation = 11

    def __init__(self, collector_wallet: KeyPair, contract_address: str) -> None:
        self.collector_wallet = collector_wallet
        self.web3 = Web3(Web3.HTTPProvider(node_url))
        self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)
        with open(Path('routes') / 'utility' / 'obt_abi.json', 'r') as abi_file:
            self.contract_instance = self.web3.eth.contract(self.web3.toChecksumAddress(contract_address), abi=json.load(abi_file))
        self.owner_address = self.contract_instance.functions.owner().call()

    def get_balance(self, address: str) -> float:
        return self.contract_instance.functions.balanceOf(self.web3.toChecksumAddress(address)).call()

    def get_bnb_balance(self,address:str) -> float:
        return self.web3.eth.get_balance(self.web3.toChecksumAddress(address))

    def transfer(self, from_address: str, from_address_private_key: bytes, to_address: str, amount: int) -> Result:
        try:

            trx = self.contract_instance.functions.transfer(to_address, int(amount)).buildTransaction({
                'from':  from_address,
                'nonce': self.web3.eth.getTransactionCount(from_address)
            })
            signed_txn = self.web3.eth.account.sign_transaction(trx, private_key=CipherAES.decrypt(from_address_private_key))

            return Ok(self.web3.toHex(self.web3.eth.send_raw_transaction(signed_txn.rawTransaction)))
        except Exception as e:
            return Err(str(e))

    def transfer_from(self, from_address: str, amount: int) -> Result:
        try:
            trx = self.contract_instance.functions.transferFrom(from_address, self.collector_wallet.pub, int(amount)).buildTransaction({
                'from':  self.collector_wallet.pub,
                'nonce': self.web3.eth.getTransactionCount(self.collector_wallet.pub)
            })
            signed_txn = self.web3.eth.account.sign_transaction(trx, private_key=CipherAES.decrypt(self.collector_wallet.priv))

            return Ok(self.web3.toHex(self.web3.eth.send_raw_transaction(signed_txn.rawTransaction)))
        except Exception as e:
            return Err(str(e))

    def approve(self, address: KeyPair) -> Result:
        try:
            _estimate = self._estimate_transaction_fee()
            trx = self.contract_instance.functions.approve(self.collector_wallet.pub, self.max_token).buildTransaction({
                'from': address[0],
                'nonce': self.web3.eth.getTransactionCount(address[0]),
                'gas': _estimate['gas'],
                'gasPrice': _estimate['gasPrice'],
            })

            signed_txn = self.web3.eth.account.sign_transaction(trx, private_key=CipherAES.decrypt(address[-1]))
            return Ok(self.web3.toHex(self.web3.eth.send_raw_transaction(signed_txn.rawTransaction)))
        except Exception as e:
            return Err(str(e))

    def transfer_gas_fee(self, to_address: str) -> Result:
        try:
            tx = {
                'nonce': self.web3.eth.getTransactionCount(self.collector_wallet.pub),
                'to': to_address,
                **self._estimate_transaction_fee()
            }

            signed_txn = self.web3.eth.account.signTransaction(tx, CipherAES.decrypt(self.collector_wallet.priv))
            return Ok(self.web3.toHex(self.web3.eth.send_raw_transaction(signed_txn.rawTransaction)))
        except Exception as e:
            return Err(str(e))

    @staticmethod
    def generate_wallet(root_key: bytes, derivation_material: bytes) -> KeyPair:
        assert len(root_key) >= 32 and len(derivation_material) > 0, "Key material not in order"
        dm = BLAKE2b.new(digest_bytes=len(root_key))
        dm.update(derivation_material)
        key = HKDF(root_key + dm.digest(), key_len=32, salt=b's'*64, hashmod=SHA512, context=b"Sub-wallets", num_keys=1)
        acct = Account.from_key(key)
        return KeyPair(acct.address, CipherAES.encrypt(key))

    def _estimate_transaction_fee(self):
        trx = {}
        trx['gasPrice'] = int(self.web3.eth.gasPrice) if int(self.web3.eth.gasPrice) > 0 else self.web3.toWei('10', 'gwei')
        trx['gas'] = self.web3.eth.estimate_gas(trx)
        # The gas limit x gas price = gas fee, which is what you have to pay for the transaction to be executed
        trx['value'] = trx['gasPrice'] * trx['gas']

        return trx

    def is_transaction_confirmed(self, trxHash: str) -> bool:
        if trxHash is None:
            return False

        try:
            # TODO: handle revert seperately from tx-not-found
            return bool(self.web3.eth.get_transaction_receipt(trxHash).get('status'))
        except TransactionNotFound:
            return False

