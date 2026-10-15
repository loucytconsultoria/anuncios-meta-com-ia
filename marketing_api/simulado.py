# -*- coding: utf-8 -*-
"""Conta de anúncios simulada em memória (ou num arquivo JSON), com a mesma interface do Cliente.

Serve para rodar o fluxo inteiro offline — validar, aplicar, aplicar de novo (idempotência),
provar e auditar — sem token e sem tocar em conta real. Cobre só o necessário para o fluxo:
campanhas, conjuntos, criativos e anúncios, com leitura por nome e validate_only.

Para demonstrar a regra de "não repetir POST às cegas", falhar_rede_no_post=N faz o N-ésimo POST
de criação CRIAR o objeto mas devolver erro de rede, como acontece de verdade quando a conexão cai
depois que a Meta já gravou.
"""
import itertools
import json
import os

try:
    from .cliente import REDE
except ImportError:
    from cliente import REDE

_contador = itertools.count(1000)


class ClienteSimulado:
    def __init__(self, conta='act_0', arquivo=None, falhar_rede_no_post=None, verbose=False):
        self.conta = conta
        self.token = 'simulado'
        self.arquivo = arquivo
        self.verbose = verbose
        self.falhar = falhar_rede_no_post
        self.posts = 0
        self.chamadas = 0
        self.objs = {'campaigns': {}, 'adsets': {}, 'adcreatives': {}, 'ads': {}}
        if arquivo and os.path.exists(arquivo):
            with open(arquivo, encoding='utf-8') as f:
                self.objs = json.load(f)

    def pronto(self):
        return True

    def _salvar(self):
        if self.arquivo:
            with open(self.arquivo, 'w', encoding='utf-8') as f:
                json.dump(self.objs, f, ensure_ascii=False, indent=1)

    def chamar(self, caminho, dados, metodo='POST'):
        self.chamadas += 1
        partes = caminho.split('/')
        if len(partes) == 2 and partes[1] in self.objs:
            tipo = partes[1]
            if 'validate_only' in (dados.get('execution_options') or []):
                faltam = [k for k in self._obrigatorios(tipo) if k not in dados]
                if faltam:
                    return None, {'message': 'parâmetro obrigatório ausente: %s' % ', '.join(faltam), 'code': 100}
                return {'success': True}, None
            faltam = [k for k in self._obrigatorios(tipo) if k not in dados]
            if faltam:
                return None, {'message': 'parâmetro obrigatório ausente: %s' % ', '.join(faltam), 'code': 100}
            novo = str(next(_contador))
            obj = json.loads(json.dumps(dados))
            obj['id'] = novo
            if tipo == 'ads' and 'creative' in obj:
                obj['creative'] = self.objs['adcreatives'].get(str(obj['creative'].get('creative_id')), obj['creative'])
            self.objs[tipo][novo] = obj
            self._salvar()
            self.posts += 1
            if self.falhar and self.posts == self.falhar:
                return None, {'message': 'rede/timeout: conexão encerrada (simulado)', REDE: True}
            return {'id': novo}, None
        # edição de um objeto existente
        for tipo, col in self.objs.items():
            if caminho in col:
                col[caminho].update({k: v for k, v in dados.items() if k != 'execution_options'})
                self._salvar()
                return {'success': True}, None
        return None, {'message': 'objeto não encontrado: %s' % caminho, 'code': 803}

    @staticmethod
    def _obrigatorios(tipo):
        return {'campaigns': ['name', 'objective', 'status'],
                'adsets': ['name', 'campaign_id', 'optimization_goal', 'billing_event', 'targeting', 'status'],
                'adcreatives': ['name'],
                'ads': ['name', 'adset_id', 'creative', 'status']}[tipo]

    def buscar(self, caminho, params=None):
        self.chamadas += 1
        partes = caminho.split('/')
        if len(partes) == 2 and partes[1] in self.objs:
            dados = list(self.objs[partes[1]].values())
            if partes[0] != self.conta:              # /<campanha>/adsets ou /<conjunto>/ads
                chave = 'campaign_id' if partes[1] == 'adsets' else 'adset_id'
                dados = [d for d in dados if str(d.get(chave)) == partes[0]]
            return {'data': dados, 'paging': {}}, None
        if caminho == self.conta:
            return {'name': 'Conta simulada', 'account_id': self.conta.replace('act_', ''), 'account_status': 1,
                    'currency': 'BRL', 'timezone_name': 'America/Sao_Paulo'}, None
        for col in self.objs.values():
            if caminho in col:
                return col[caminho], None
        return None, {'message': 'objeto não encontrado', 'code': 803}

    def paginar(self, caminho, params=None, max_paginas=50):
        d, err = self.buscar(caminho, params)
        return (d or {}).get('data', []), err
