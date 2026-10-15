# -*- coding: utf-8 -*-
"""Vocabulário fechado: o contrato entre quem classifica (o modelo) e quem conta (o script).

O arquivo de configuração (JSON) define, por campo, os códigos permitidos e quando usar cada um.
O mesmo arquivo é lido pelo subagente (que precisa saber o que marcar) e por este módulo (que
valida o que voltou). Formato:

{
  "nome": "...", "unidade": "o que é um item",
  "campos": {
    "temas": {"tipo": "lista", "max": 3, "codigos": {"codigo": "quando usar", ..., "outro": "..."}},
    "tom":   {"tipo": "um",    "codigos": {...}},
    "elementos_visuais": {"tipo": "lista", "so_imagem": true, "codigos": {...}}
  },
  "campos_livres": {"texto_na_imagem": "instrução", ...},
  "regras": ["...", "..."]
}

Código fora do vocabulário não derruba o lote: vira 'outro' (se o campo tiver 'outro') e o termo
original vai para '<campo>_outros', para revisão humana e eventual ampliação do vocabulário.
"""
import json


class Vocabulario:
    def __init__(self, dados):
        self.dados = dados
        self.nome = dados.get('nome', '')
        self.campos = dados['campos']
        self.livres = dados.get('campos_livres', {})

    @classmethod
    def carregar(cls, caminho):
        with open(caminho, encoding='utf-8') as f:
            return cls(json.load(f))

    def codigos(self, campo):
        return set(self.campos[campo]['codigos'])

    def campos_para(self, tipo_item):
        """Campos classificados para 'img' ou 'txt' (campos so_imagem ficam fora do texto)."""
        return [c for c, d in self.campos.items() if tipo_item == 'img' or not d.get('so_imagem')]

    def limpar(self, registro, tipo_item='img'):
        """Normaliza um registro devolvido pelo modelo. Devolve (registro, n_codigos_corrigidos)."""
        d = dict(registro)
        corrigidos = 0
        for campo in self.campos_para(tipo_item):
            spec = self.campos[campo]
            validos = set(spec['codigos'])
            extras = []
            if spec['tipo'] == 'um':
                v = str(d.get(campo) or '').strip().lower()
                if v and v not in validos:
                    extras.append(v)
                    v = 'outro' if 'outro' in validos else ''
                d[campo] = v
            else:
                bruto = d.get(campo) or []
                if isinstance(bruto, str):
                    bruto = [bruto]
                out = []
                for x in bruto:
                    x = str(x).strip().lower()
                    if x in validos:
                        if x not in out:
                            out.append(x)
                    elif x:
                        extras.append(x)
                        if 'outro' in validos and 'outro' not in out:
                            out.append('outro')
                if spec.get('max'):
                    out = out[:spec['max']]
                d[campo] = out
            if extras:
                corrigidos += len(extras)
                k = campo + '_outros'
                d[k] = '; '.join(x for x in [d.get(k, '')] + extras if x)
        return d, corrigidos

    def descrever(self):
        """Texto curto do vocabulário, para logs e relatórios."""
        partes = ['%s: %d códigos' % (c, len(d['codigos'])) for c, d in self.campos.items()]
        return '%s (%s)' % (self.nome, '; '.join(partes))
