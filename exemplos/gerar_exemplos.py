# -*- coding: utf-8 -*-
"""Gera os dados SINTÉTICOS de exemplo (entidades, anúncios, imagens e leituras simuladas).

Tudo aqui é inventado: entidades, documentos (com dígito verificador propositalmente inválido,
para nunca coincidir com um cadastro real), páginas, textos e números. A região "Meridiano" é
fictícia. Rodar de novo sobrescreve os arquivos com o mesmo conteúdo.

Uso: python exemplos/gerar_exemplos.py   (requer Pillow para as imagens)
"""
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
from classificacao.montar_lotes import txt_id  # noqa: E402


def doc_invalido(base12):
    """Documento de 14 dígitos com dígitos verificadores calculados e depois alterados (inválido)."""
    def dv(nums, pesos):
        s = sum(n * p for n, p in zip(nums, pesos)) % 11
        return 0 if s < 2 else 11 - s
    n = [int(c) for c in base12]
    d1 = dv(n, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(n + [d1], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = (d2 + 1) % 10                       # quebra o verificador de propósito
    s = base12 + str(d1) + str(d2)
    return '%s.%s.%s/%s-%s' % (s[:2], s[2:5], s[5:8], s[8:12], s[12:])


ENT = [
    ('E1', 'INSTITUTO AGUACLARA DE SANEAMENTO', 'AGUACLARA', '901010100001'),
    ('E2', 'ASSOCIACAO PEDAL MERIDIANO', 'PEDAL MERIDIANO', '902020200001'),
    ('E3', 'REDE ESCOLA ABERTA MERIDIANO', 'ESCOLA ABERTA', '903030300001'),
    ('E4', 'CLINICA COMUNITARIA BEM VIVER', 'BEM VIVER', '904040400001'),
    ('E5', 'COLETIVO SERRA VERDE', 'SERRA VERDE', '905050500001'),
    ('E6', 'OBSERVATORIO RUA SEGURA', 'RUA SEGURA', '906060600001'),
]
DOC = {e[0]: doc_invalido(e[3]) for e in ENT}
DOC_TERCEIRO = doc_invalido('907070700001')
PAG = {'E1': ('7001', 'Aguaclara Saneamento'), 'E2': ('7002', 'Pedal Meridiano'), 'E3': ('7003', 'Escola Aberta'),
       'E4': ('7004', 'Bem Viver Saúde'), 'E5': ('7005', 'Serra Verde'), 'E6': ('7006', 'Observatório Rua Segura'),
       'X': ('7999', 'Classificados do Vale')}


def faixa(lo, hi):
    return {'lower_bound': str(lo), 'upper_bound': str(hi)} if hi is not None else {'lower_bound': str(lo)}


def regiao(pct_meridiano):
    return [{'region': 'Meridiano', 'percentage': '%.3f' % pct_meridiano},
            {'region': 'Outra região', 'percentage': '%.3f' % (1 - pct_meridiano)}]


def ad(i, ent, byline, texto, gasto, impr, inicio, fim=None, pct=0.95, titulo='', fonte=None, plataformas=('facebook', 'instagram')):
    pid, pnome = PAG[ent]
    a = {'id': i, 'page_id': pid, 'page_name': pnome, 'bylines': byline,
         'ad_creation_time': inicio, 'ad_delivery_start_time': inicio, 'ad_creative_bodies': [texto],
         'ad_creative_link_titles': [titulo] if titulo else [], 'spend': faixa(*gasto), 'impressions': faixa(*impr),
         'currency': 'BRL', 'publisher_platforms': list(plataformas), 'delivery_by_region': regiao(pct),
         'ad_snapshot_url': 'https://www.facebook.com/ads/library/?id=%s' % i}
    if fim:
        a['ad_delivery_stop_time'] = fim
    if fonte:
        a['_fonte'] = fonte
    return a


def pago(e, com_doc=True):
    nome = dict((x[0], x[1]) for x in ENT)[e]
    return 'PAGO POR %s%s' % (nome, (' - DOC %s' % DOC[e]) if com_doc else '')


def anuncios():
    base = [
        ad('a1001', 'E1', pago('E1'), 'Água tratada em todas as torneiras do bairro: veja o mapa das obras.', (1000, 1499), (40000, 44999), '2026-08-20', '2026-09-10', fonte='documento:E1'),
        ad('a1002', 'E1', pago('E1'), 'Saneamento é saúde. Conheça o plano de coleta de esgoto.', (100, 199), (8000, 8999), '2026-08-22', fonte='documento:E1'),
        ad('a1003', 'E2', pago('E2'), 'Ciclovia contínua do centro à periferia. Assine a proposta.', (500, 599), (20000, 24999), '2026-08-25', '2026-09-20', fonte='documento:E2'),
        ad('a1004', 'E3', pago('E3'), 'Escola em tempo integral: matrículas abertas para oficinas gratuitas.', (300, 399), (15000, 19999), '2026-08-18', fonte='documento:E3'),
        ad('a1005', 'E3', pago('E3'), 'Leitura todo dia: biblioteca comunitária aberta aos sábados.', (0, 99), (2000, 2999), '2026-09-01', '2026-09-05', fonte='documento:E3'),
        ad('a1006', 'E4', 'COMUNICACAO INSTITUCIONAL', 'Mutirão de exames preventivos. Inscrição: DOC %s' % DOC['E4'], (200, 299), (9000, 9999), '2026-08-28', fonte='documento:E4'),
        ad('a1007', 'E2', 'PAGO POR ASSOCIACAO PEDAL MERIDIANO', 'Bicicletário seguro em cada terminal de ônibus.', (100, 199), (6000, 6999), '2026-09-02'),
        ad('a1008', 'X', 'PAGO POR ASSOCIACAO PEDAL MERIDIANO DO NORTE', 'Passeio ciclístico de domingo, inscrições abertas.', (0, 99), (1000, 1999), '2026-09-03', pct=0.9),
        ad('a1009', 'E5', 'SERRA VERDE', 'Plantio de mudas nativas na encosta. Venha com a família.', (0, 99), (3000, 3999), '2026-09-04'),
        ad('a1010', 'X', 'PAGO POR COLETIVO SERRA VERDE', 'Feira de artesanato no litoral, sábado e domingo.', (0, 99), (1000, 1999), '2026-09-04', pct=0.1),
        ad('a1011', 'E6', pago('E6'), 'Iluminação pública: mapa dos pontos escuros que denunciamos.', (400, 499), (18000, 19999), '2026-08-19', fonte='documento:E6'),
        ad('a1012', 'E6', '', 'Mais luz, mais segurança: participe da caminhada noturna.', (100, 199), (5000, 5999), '2026-09-06'),
        ad('a1013', 'E6', 'PAGO POR FUNDACAO HORIZONTE - DOC %s' % DOC_TERCEIRO, 'Evento de parceiro na página: palestra sobre urbanismo.', (100, 199), (4000, 4999), '2026-09-07'),
        ad('a1014', 'E3', 'ESCOLA ABERTA MERIDIANO', 'Reforço escolar gratuito: inscreva seu filho.', (0, 99), (2000, 2999), '2026-09-08'),
        ad('a1015', 'X', 'CLASSIFICADOS DO VALE', 'Vendo bicicleta aro 29, pouco uso.', (0, 99), (1000, 1999), '2026-09-09'),
        ad('a1016', 'E4', 'BV SAUDE', 'Vacinação em dia: confira o calendário da campanha.', (100, 199), (7000, 7999), '2026-09-10', fonte='documento:E4'),
        ad('a1017', 'E1', pago('E1'), 'Ligação de esgoto sem custo para famílias de baixa renda.', (0, 99), (9000, 9999), '2026-09-11', fonte='documento:E1'),
    ]
    # a varredura por nome repete a1007 (já completo) e traz a1018, só com campos leves
    leves = [
        {k: v for k, v in base[6].items() if k in ('id', 'page_id', 'page_name', 'bylines', 'ad_delivery_start_time')},
        {'id': 'a1018', 'page_id': '7002', 'page_name': 'Pedal Meridiano', 'bylines': 'PAGO POR ASSOCIACAO PEDAL MERIDIANO',
         'ad_delivery_start_time': '2026-09-12'},
    ]
    # a atualização traz a1001 e a1002 de novo (faixas maiores: continuaram no ar), a1004 igual e um anúncio novo
    atual = [
        ad('a1001', 'E1', pago('E1'), base[0]['ad_creative_bodies'][0], (1500, 1999), (50000, 59999), '2026-08-20', '2026-09-10', fonte='documento:E1'),
        ad('a1002', 'E1', pago('E1'), base[1]['ad_creative_bodies'][0], (200, 299), (12000, 12999), '2026-08-22', fonte='documento:E1'),
        base[3],
        ad('a1019', 'E1', pago('E1'), 'Saneamento é saúde. Conheça o plano de coleta de esgoto.', (100, 199), (7000, 7999), '2026-09-15', fonte='documento:E1'),
        ad('a1020', 'E5', pago('E5'), 'Mutirão de limpeza do rio: ponto de encontro na ponte.', (200, 299), (10000, 10999), '2026-09-16', fonte='documento:E5'),
    ]
    return base, leves, atual


# imagem -> (cor de fundo, linhas de texto); arquivos com o mesmo desenho são a mesma arte em outro tamanho
ARTES = {
    'arte_agua': ((24, 98, 160), ['ÁGUA TRATADA', 'EM TODAS AS TORNEIRAS', 'Instituto Aguaclara']),
    'arte_esgoto': ((40, 120, 90), ['SANEAMENTO', 'É SAÚDE', 'Plano de coleta de esgoto']),
    'arte_ciclovia': ((200, 90, 40), ['CICLOVIA CONTÍNUA', 'do centro à periferia', 'Assine a proposta']),
    'arte_escola': ((120, 60, 150), ['ESCOLA EM', 'TEMPO INTEGRAL', 'Oficinas gratuitas']),
    'arte_biblioteca': ((150, 110, 50), ['LEITURA TODO DIA', 'Biblioteca aberta aos sábados']),
    'arte_exames': ((30, 140, 150), ['MUTIRÃO DE EXAMES', 'Inscrições abertas']),
    'arte_mudas': ((60, 130, 50), ['PLANTIO DE MUDAS', 'NATIVAS', 'Venha com a família']),
    'arte_luz': ((30, 30, 60), ['MAIS LUZ', 'MAIS SEGURANÇA', 'Caminhada noturna']),
    'arte_vacina': ((180, 50, 70), ['VACINAÇÃO EM DIA', 'Confira o calendário']),
    'arte_rio': ((20, 90, 120), ['MUTIRÃO DE LIMPEZA', 'DO RIO', 'Encontro na ponte']),
}
# anúncio -> arquivos de imagem (as cópias "_grande" são a mesma arte em outro tamanho)
CRIATIVOS = {
    'a1001': ['arte_agua.png'], 'a1002': ['arte_esgoto.png'], 'a1019': ['arte_esgoto_grande.png'],
    'a1017': ['arte_esgoto.png'], 'a1003': ['arte_ciclovia.png'], 'a1007': ['arte_ciclovia_grande.png'],
    'a1004': ['arte_escola.png'], 'a1005': ['arte_biblioteca.png'], 'a1014': ['arte_escola_grande.png'],
    'a1006': ['arte_exames.png'], 'a1016': ['arte_vacina.png'], 'a1009': ['arte_mudas.png'],
    'a1020': ['arte_rio.png'], 'a1011': ['arte_luz.png'], 'a1012': ['arte_luz.png'],
}

# gabarito do que um leitor diria de cada arte (leituras simuladas)
LEITURA = {
    'arte_agua': (['saneamento_agua'], 'propositivo', ['acesse_site'], 'card azul com texto branco centralizado'),
    'arte_esgoto': (['saneamento_agua', 'saude'], 'institucional_informativo', ['nenhum'], 'card verde com texto em caixa alta'),
    'arte_ciclovia': (['transporte_publico'], 'convocacao_mobilizacao', ['assine_participe'], 'card laranja com seta desenhada'),
    'arte_escola': (['educacao'], 'propositivo', ['inscreva_se'], 'card roxo com texto centralizado'),
    'arte_biblioteca': (['educacao', 'cultura_lazer'], 'institucional_informativo', ['nenhum'], 'card marrom com texto'),
    'arte_exames': (['saude'], 'convocacao_mobilizacao', ['inscreva_se'], 'card azul-esverdeado com texto'),
    'arte_mudas': (['meio_ambiente'], 'convocacao_mobilizacao', ['participe_evento'], 'card verde com texto'),
    'arte_luz': (['seguranca_iluminacao'], 'denuncia_critica', ['participe_evento'], 'card escuro com texto claro'),
    'arte_vacina': (['saude'], 'institucional_informativo', ['nenhum'], 'card vermelho com texto'),
    'arte_rio': (['meio_ambiente'], 'convocacao_mobilizacao', ['participe_evento'], 'card azul com texto'),
}


def _fonte(tamanho):
    """Fonte do sistema com acentos (Arial no Windows, DejaVu no Linux, Helvetica no macOS).
    Nenhuma fonte é distribuída com o repositório. Sem nenhuma delas, cai na fonte padrão do Pillow,
    que não tem acentos: as imagens saem sem acento e a transcrição simulada deixa de bater letra a letra."""
    from PIL import ImageFont
    for nome in ('arial.ttf', 'Arial.ttf', 'DejaVuSans.ttf', 'Helvetica.ttc', 'LiberationSans-Regular.ttf'):
        try:
            return ImageFont.truetype(nome, tamanho), True
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tamanho), False
    except TypeError:
        return ImageFont.load_default(), False


def _sem_acento(t):
    import unicodedata
    return unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()


def gerar_imagens(pasta):
    from PIL import Image, ImageDraw
    os.makedirs(pasta, exist_ok=True)
    fonte, acentos = _fonte(34)
    pequena, _ = _fonte(15)
    txt = (lambda t: t) if acentos else _sem_acento
    for nome, (cor, linhas) in ARTES.items():
        im = Image.new('RGB', (540, 540), cor)
        d = ImageDraw.Draw(im)
        d.rectangle([30, 30, 510, 510], outline=(255, 255, 255), width=6)
        y = 170
        for l in linhas:
            w = d.textlength(txt(l), font=fonte)
            d.text(((540 - w) / 2, y), txt(l), fill=(255, 255, 255), font=fonte)
            y += 60
        d.text((44, 478), txt('Anúncio de exemplo (dados sintéticos)'), fill=(230, 230, 230), font=pequena)
        im.save(os.path.join(pasta, nome + '.png'))
        if any(nome + '_grande.png' in v for v in CRIATIVOS.values()):
            im.resize((1080, 1080), Image.LANCZOS).save(os.path.join(pasta, nome + '_grande.png'))


def gravar_jsonl(caminho, objs):
    with open(caminho, 'w', encoding='utf-8') as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=False) + '\n')


def main():
    with open(os.path.join(AQUI, 'entidades.csv'), 'w', encoding='utf-8', newline='') as f:
        f.write('id,nome,apelido,documento\n')
        for e in ENT:
            f.write('%s,%s,%s,%s\n' % (e[0], e[1], e[2], DOC[e[0]]))
    col = os.path.join(AQUI, 'coleta')
    os.makedirs(col, exist_ok=True)
    base, leves, atual = anuncios()
    gravar_jsonl(os.path.join(col, 'ads_completos.jsonl'), base)
    gravar_jsonl(os.path.join(col, 'varredura_nomes.jsonl'), leves)
    gravar_jsonl(os.path.join(col, 'ads_completos_zatual.jsonl'), atual)
    with open(os.path.join(AQUI, 'criativos.json'), 'w', encoding='utf-8') as f:
        json.dump(CRIATIVOS, f, ensure_ascii=False, indent=1)
    gerar_imagens(os.path.join(AQUI, 'imagens'))

    # leituras simuladas: principal e releitura de controle (com pequenas diferenças realistas)
    lei = os.path.join(AQUI, 'leituras')
    os.makedirs(lei, exist_ok=True)
    principal, releitura = {}, {}
    for nome, (temas, tom, cta, desc) in LEITURA.items():
        texto = ' / '.join(ARTES[nome][1]) + ' / Anúncio de exemplo (dados sintéticos)'
        r = {'arquivo': nome, 'texto_na_imagem': texto, 'tipo_visual': 'arte_grafica', 'elementos_visuais': ['texto_dominante'],
             'temas': temas, 'temas_outros': '', 'tom': tom, 'cta': cta, 'descricao_visual': desc}
        principal[nome] = r
        r2 = dict(r)
        if nome == 'arte_esgoto':
            r2['temas'] = ['saneamento_agua']                     # releitura marcou só um tema
        if nome in ('arte_agua', 'arte_ciclovia', 'arte_escola'):
            r2['texto_na_imagem'] = ' / '.join(ARTES[nome][1])                       # pulou o rodapé pequeno
        if nome == 'arte_ciclovia':
            r2['temas'] = ['mobilidade']
        if nome == 'arte_luz':
            r2['tom'] = 'medo_alerta'
            r2['texto_na_imagem'] = 'MAIS LUZ / MAIS SEGURANÇA / Caminhada noturna'    # pulou o texto pequeno
        releitura[nome] = r2
    txt_principal = {}
    for a in base + atual:
        t = a['ad_creative_bodies'][0]
        k = txt_id(t, '')
        low = t.lower()
        temas = (['saneamento_agua'] if 'esgoto' in low or 'água' in low or 'saneamento' in low else
                 ['mobilidade'] if 'ciclo' in low or 'bicic' in low else
                 ['educacao'] if 'escola' in low or 'leitura' in low or 'reforço' in low else
                 ['saude'] if 'exame' in low or 'vacina' in low else
                 ['meio_ambiente'] if 'muda' in low or 'rio' in low else
                 ['seguranca_iluminacao'] if 'luz' in low or 'ilumina' in low else [])
        txt_principal[k] = {'txt': k, 'temas': temas, 'temas_outros': '', 'tom': 'propositivo', 'cta': ['nenhum']}
    with open(os.path.join(lei, 'imagens_principal.json'), 'w', encoding='utf-8') as f:
        json.dump(principal, f, ensure_ascii=False, indent=1)
    with open(os.path.join(lei, 'imagens_releitura.json'), 'w', encoding='utf-8') as f:
        json.dump(releitura, f, ensure_ascii=False, indent=1)
    with open(os.path.join(lei, 'textos_principal.json'), 'w', encoding='utf-8') as f:
        json.dump(txt_principal, f, ensure_ascii=False, indent=1)
    print('exemplos gerados em %s' % AQUI)


if __name__ == '__main__':
    main()
