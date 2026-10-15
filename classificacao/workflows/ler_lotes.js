// Orquestração com Claude Code (ferramenta Workflow): uma onda de leitura de lotes.
//
// Um subagente por lote. Lotes de imagem seguem o protocolo um a um (abre UMA imagem, grava o
// resultado dela no arquivo com o NOME DA IMAGEM, só então abre a próxima): é o que permite a
// auditoria de alinhamento (auditar_alinhamento.py) provar, pela transcrição, que cada resultado
// veio da imagem certa. Lotes de texto gravam um JSON Lines no fim.
//
// args:
//   trabalho     pasta de trabalho (a mesma de montar_lotes.py), caminho absoluto
//   vocabulario  caminho absoluto do vocabulário fechado (JSON)
//   contexto     uma linha sobre o material (ex.: "anúncios de um mercado de serviços")
//   lotes        lista de nomes de lote (ex.: ["img_00001", "txt_00001", "qa_00001"]); ou
//   faixas       [{tipo: "img"|"qa"|"txt"|"qatxt", de: 1, ate: 40, partes?: 2, tam?: 12, modelo?}]
//   modelo       modelo padrão dos leitores (padrão: "sonnet"; imagens pedem modelo com boa visão)

export const meta = {
  name: 'ler-lotes',
  description: 'Subagentes leem lotes de imagens e textos e gravam a classificação em disco, um arquivo por imagem',
  whenToUse: 'Depois de montar_lotes.py. Depois da onda: auditar_alinhamento.py e validar_lotes.py.',
  phases: [{ title: 'Leitura', detail: 'um subagente por lote (ou por parte de lote)' }],
}

const T = args.trabalho
const VOC = args.vocabulario
const CTX = args.contexto || 'anúncios'

function promptImg(sub, nome, parte) {
  const faixa = parte
    ? `\n2b. Processe SOMENTE os itens de número ${parte[0]} a ${parte[1]} do manifesto (contando do 1). Os demais são de outro leitor: não os abra.`
    : ''
  const dir = `${T}/resultados_${sub}/${nome}`
  return `Tarefa mecânica de leitura de imagens (${CTX}) para uma base de dados. Não leia nenhum arquivo além dos indicados.

1. Leia o vocabulário fechado: ${VOC}
2. Leia o manifesto: ${T}/lotes_${sub}/${nome}.json (lista de itens com "caminho" de imagens).${faixa}
3. Processe as imagens em ALTERNÂNCIA ESTRITA, uma de cada vez:
   a) abra UMA imagem com a ferramenta Read (caminho absoluto), sozinha na mensagem;
   b) na mensagem seguinte, grave SOMENTE o resultado dessa imagem com a ferramenta Write, no arquivo
      ${dir}/<NOME>.json, onde <NOME> é o nome do arquivo de imagem que você ACABOU de abrir, sem a extensão;
   c) só então abra a próxima imagem.
   Proibido: abrir duas imagens seguidas sem gravar; pôr Read e Write na mesma mensagem; gravar resultado de
   imagem que não foi a última aberta. Imagens parecidas são comuns: descreva só a que acabou de abrir.
4. Cada arquivo contém UM objeto JSON com:
   - "arquivo": o <NOME> (igual ao nome do .json)
   - "texto_na_imagem": TODO o texto visível, literal, na ordem de leitura, com acentos e maiúsculas como aparecem.
     Inclua texto pequeno (rodapés, selos, legendas queimadas). Blocos separados por " / ". Ilegível: [ilegível]. Sem texto: "".
   - um campo para cada campo classificado do vocabulário, só com códigos do vocabulário
     (campo "um": um código; campo "lista": lista de códigos, respeitando o máximo)
   - "temas_outros": texto curto ou "" (assunto que não coube em nenhum código)
   - "descricao_visual": uma frase objetiva do que se vê (enquadramento, pessoas, cenário, cores), sem repetir o texto
   Nunca identifique pessoa pelo rosto. Se a imagem não abrir: {"arquivo": ..., "erro": "motivo"}.
5. NUNCA mande uma mensagem sem chamar uma ferramenta: cada mensagem sua é um Read ou um Write, sem aviso, plano
   ou resumo. Mensagem só de texto encerra a tarefa e perde o lote.
6. Resposta final (só depois de gravar todos os arquivos): "OK <número de arquivos gravados>".`
}

function promptTxt(sub, nome) {
  return `Tarefa mecânica de classificação de textos (${CTX}) para uma base de dados. Não leia nenhum arquivo além dos indicados.

1. Leia o vocabulário fechado: ${VOC}
2. Leia o manifesto: ${T}/lotes_${sub}/${nome}.json (cada item tem "txt", "texto" e "titulo"; "---" separa variações do mesmo item).
3. Para CADA item produza um objeto JSON com:
   - "txt": o valor "txt" do item (copie exatamente)
   - um campo para cada campo classificado do vocabulário que não seja só de imagem, só com códigos do vocabulário
   - "temas_outros": texto curto ou ""
4. Grave TODAS as linhas em ${T}/resultados_${sub}/${nome}.jsonl (JSON Lines, UTF-8, um objeto por linha, na ordem do
   manifesto, sem texto extra) com UMA chamada da ferramenta Write no final.
5. NUNCA mande uma mensagem sem chamar uma ferramenta até terminar: mensagem só de texto encerra a tarefa.
6. Resposta final (depois de gravar): "OK <número de linhas gravadas>".`
}

phase('Leitura')
const lotes = (args.lotes || []).map((nome) => ({ sub: nome.split('_')[0], nome, parte: null, rotulo: nome }))
for (const f of (args.faixas || [])) {
  for (let n = f.de; n <= f.ate; n++) {
    const nome = f.tipo + '_' + String(n).padStart(5, '0')
    // lote grande estoura o limite de turnos do leitor (uma mensagem por imagem + uma por gravação):
    // "partes" divide o MESMO lote entre leitores, cada um com sua faixa de itens
    const partes = f.partes || 1
    const tam = f.tam || 12
    for (let k = 0; k < partes; k++) {
      const de = Math.floor((k * tam) / partes) + 1
      const ate = Math.floor(((k + 1) * tam) / partes)
      lotes.push({ sub: f.tipo, nome, modelo: f.modelo, parte: partes > 1 ? [de, ate] : null,
                   rotulo: partes > 1 ? `${nome}:${de}-${ate}` : nome })
    }
  }
}
log(`${lotes.length} lotes nesta onda`)

const res = await pipeline(lotes, (l) => {
  const ehImagem = l.sub === 'img' || l.sub === 'qa'
  const p = ehImagem ? promptImg(l.sub, l.nome, l.parte) : promptTxt(l.sub, l.nome)
  return agent(p, { label: `${l.sub}:${l.rotulo}`, phase: 'Leitura', model: l.modelo || args.modelo || 'sonnet', effort: 'low' })
})
const ok = res.filter((r) => typeof r === 'string' && r.trim().startsWith('OK')).length
return { lotes: lotes.length, ok, falhas: lotes.length - ok }
