# -*- coding: utf-8 -*-
"""Atribuição de cada anúncio a uma entidade anunciante (empresa, organização, marca).

A busca da Meta é por palavras soltas: buscar "Costa Alimentos" devolve qualquer "Costa".
Numa coleta real, a maior parte do que a busca devolve é de terceiros, então sem um filtro
explícito qualquer contagem vira ficção. Os sinais, do mais forte ao mais fraco:

  doc_rodape        documento (ex.: 14 dígitos) da entidade no rodapé "pago por" (campo bylines)
  doc_texto         documento no texto do anúncio, com rodapé sem outro documento e sem termo excludente
  nome_rodape       nome completo da entidade no rodapé, TERMINANDO ali (seguido de sufixo de fim,
                    documento, número ou fim do rodapé). Sem essa trava, "COSTA ALIMENTOS" casaria
                    com "COSTA ALIMENTOS DO NORTE", que é outra empresa.
  apelido_rodape    nome curto (marca) ABRINDO o rodapé, terminando ali
  busca_documento   (atribuído na consolidação) a própria busca pelo documento devolveu o anúncio
  pagina            página que já veiculou anúncio confirmado (sinais acima) de UMA só entidade, e o
                    rodapé deste anúncio (ou, sem rodapé, o nome da página) se parece com a entidade

Anúncio em página da entidade mas pago por outra (rodapé com outro documento ou termo excludente,
por exemplo uma parceria paga pelo parceiro) fica marcado 'pagina_outro_pagador' e NÃO entra nos
totais da entidade.
"""
import re

try:
    from . import biblioteca as B
except ImportError:          # execução direta como script
    import biblioteca as B

RX_DOC = re.compile(r'\d{2}\s*\.?\s*\d{3}\s*\.?\s*\d{3}\s*/?\s*\d{4}\s*-?\s*\d{2}')

CONFIG_PADRAO = {
    'digitos_documento': 14,
    # rodapé com um destes termos nunca conta para a entidade (ex.: patrocínio de terceiro)
    'termos_excludentes': ['PARCEIRO', 'COPATROCINIO'],
    # prefixos que costumam abrir o rodapé antes do nome
    'prefixos': ['PAGO POR', 'PATROCINADO POR', 'ANUNCIO DE', 'PUBLICIDADE'],
    # o que pode vir logo depois do nome sem mudar de entidade
    'fim_de_nome': ['LTDA', 'S A', 'SA', 'ME', 'EIRELI', 'CNPJ', 'DOC', 'INSCRICAO'],
    # palavras que não distinguem uma entidade de outra
    'nao_distintivas': '''DE DA DO DOS DAS E OFICIAL PAGINA BRASIL LTDA SA ME EIRELI GRUPO EMPRESA
    COMERCIO SERVICOS INSTITUTO ASSOCIACAO LOJA LOJAS CENTRO NACIONAL REDE'''.split(),
}


def documentos_em(texto, n=14):
    return {B.digitos(m) for m in RX_DOC.findall(texto or '') if len(B.digitos(m)) == n}


def texto_criativo(a):
    partes = []
    for k in ('ad_creative_bodies', 'ad_creative_link_titles', 'ad_creative_link_captions',
              'ad_creative_link_descriptions'):
        partes += [x for x in (a.get(k) or []) if x]
    return ' '.join(partes)


class Atribuidor:
    def __init__(self, entidades, config=None):
        """entidades: lista de dicts com id, nome, apelido (opcional) e documento (opcional)."""
        self.cfg = dict(CONFIG_PADRAO, **(config or {}))
        n = self.cfg['digitos_documento']
        self.n_doc = n
        self.excl = [B.norm(t) for t in self.cfg['termos_excludentes']]
        self.rx_prefixo = re.compile(r'^(%s )+' % '|'.join(re.escape(B.norm(p)) for p in self.cfg['prefixos']))
        self.rx_fim = re.compile(r'^(?:%s|\d+)(?:\s|$)' % '|'.join(re.escape(B.norm(p)) for p in self.cfg['fim_de_nome']))
        nd = set(self.cfg['nao_distintivas'])

        self.por_doc = {B.digitos(e['documento']): e['id'] for e in entidades if B.digitos(e.get('documento'))}
        nomes = {}
        for e in entidades:
            nomes.setdefault(B.norm(e['nome']), []).append(e['id'])
        # só nomes únicos e com pelo menos 2 palavras: nome de uma palavra pega homônimo demais
        self.por_nome = {k: ids[0] for k, ids in nomes.items() if len(ids) == 1 and len(k.split()) >= 2}
        self.nomes_ordenados = sorted(self.por_nome, key=len, reverse=True)
        apelidos = {}
        for e in entidades:
            if e.get('apelido'):
                apelidos.setdefault(B.norm(e['apelido']), []).append(e['id'])
        self.por_apelido = {k: ids[0] for k, ids in apelidos.items()
                            if len(ids) == 1 and (len(k.split()) >= 2 or len(k) >= 6)}
        self.apelidos_ordenados = sorted(self.por_apelido, key=len, reverse=True)
        self.tokens = {e['id']: {w for w in (B.norm(e['nome']) + ' ' + B.norm(e.get('apelido'))).split()
                                 if len(w) >= 3 and w not in nd} for e in entidades}
        self.colado = {e['id']: B.norm(e.get('apelido') or e['nome']).replace(' ', '') for e in entidades}
        self.nd = nd
        self.paginas = {}            # page_id -> {entidade} confirmadas por sinal forte

    # -------------------------------------------------------------- sinais
    def _docs_conhecidos(self, dig):
        """Entidades cujo documento aparece como substring dos dígitos, na ordem."""
        out, n = [], self.n_doc
        for i in range(len(dig) - n + 1):
            eid = self.por_doc.get(dig[i:i + n])
            if eid and eid not in out:
                out.append(eid)
        return out

    def _tem_excludente(self, nb):
        return any((' ' + t + ' ') in nb for t in self.excl)

    def sinal_forte(self, a):
        """(entidade, vínculo) pelos sinais de documento/nome, ou (None, motivo)."""
        byl = a.get('bylines') or ''
        achados = self._docs_conhecidos(B.digitos(byl))
        if achados:
            return achados[0], 'doc_rodape' + ('_multiplo' if len(achados) > 1 else '')
        docs_byl = documentos_em(byl, self.n_doc)
        nb = ' ' + B.norm(byl) + ' '
        excl = self._tem_excludente(nb)
        if not docs_byl and not excl:
            achados = self._docs_conhecidos(B.digitos(texto_criativo(a)))
            if achados:
                return achados[0], 'doc_texto'
            for nome in self.nomes_ordenados:
                k = nb.find(' ' + nome + ' ')
                if k < 0:
                    continue
                depois = nb[k + len(nome) + 2:].split()
                if not depois or self.rx_fim.match(' '.join(depois)):
                    return self.por_nome[nome], 'nome_rodape'
            so = self.rx_prefixo.sub('', nb.strip())
            for ap in self.apelidos_ordenados:
                if not (so == ap or so.startswith(ap + ' ')):
                    continue
                depois = so[len(ap):].split()
                if not depois or self.rx_fim.match(' '.join(depois)):
                    return self.por_apelido[ap], 'apelido_rodape'
        return None, ('outro_documento' if docs_byl else ('excludente' if excl else 'sem_sinal'))

    def registrar_paginas(self, confirmados):
        """confirmados: iterável de (anúncio, entidade) atribuídos por sinal forte."""
        for a, eid in confirmados:
            if a.get('page_id'):
                self.paginas.setdefault(str(a['page_id']), set()).add(eid)

    def _parece(self, eid, texto):
        """O texto (rodapé ou nome da página) compartilha palavra distintiva com a entidade?"""
        alvo = self.tokens.get(eid, set())
        t = {w for w in B.norm(texto).split() if len(w) >= 3 and w not in self.nd}
        colado = B.norm(texto).replace(' ', '')
        c = self.colado.get(eid, '')
        return bool(alvo & t) or (len(c) >= 6 and c in colado)

    def atribuir(self, a):
        """Atribuição final de um anúncio: sinais fortes e, se nenhum, a regra de página."""
        eid, v = self.sinal_forte(a)
        if eid:
            return eid, v
        donos = self.paginas.get(str(a.get('page_id') or ''))
        if donos and len(donos) == 1:
            dono = next(iter(donos))
            if v in ('outro_documento', 'excludente'):
                return None, 'pagina_outro_pagador:' + dono
            byl = (a.get('bylines') or '').strip()
            if byl:
                return (dono, 'pagina') if self._parece(dono, byl) else (None, 'pagina_outro_pagador:' + dono)
            if self._parece(dono, a.get('page_name') or ''):
                return dono, 'pagina'
            return None, 'pagina_sem_rodape_nao_parecida'
        if donos and len(donos) > 1:
            return None, 'pagina_compartilhada'
        return None, v
