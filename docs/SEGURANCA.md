# Segurança e resposta a incidente

## Certificado `assinatura.pfx` exposto

O arquivo estava rastreado na revisão inicial `826a2119910df4f81053f4453d1c0ee50d4f0f42`, apesar de `*.pfx` já constar no `.gitignore`. Regras de ignore não removem arquivos previamente rastreados. O conteúdo não foi aberto nem utilizado nesta intervenção.

A remoção do índice com `git rm --cached assinatura.pfx` interrompe o versionamento futuro, **mas não revoga o certificado nem apaga clones, forks, artefatos ou histórico já publicados**. Considere o certificado e sua senha comprometidos.

### Ações necessárias pelo proprietário

1. Revogar/reemitir o certificado junto à autoridade emissora; invalidar a chave antiga e proteger a nova fora do Git (cofre/assinatura com chave não exportável). Não reutilizar a senha antiga.
2. Inventariar quem baixou o repositório, releases, forks e caches; remover artefatos de build que incluam o arquivo.
3. Conferir assinaturas e registros de emissão/uso desde a primeira exposição. A data de início não foi estabelecida nesta intervenção.
4. **Somente após autorização explícita e coordenação com todos os mantenedores**, fazer backup do repositório e planejar `git filter-repo --invert-paths --path assinatura.pfx` para removê-lo do histórico. Validar o resultado em uma cópia antes de qualquer publicação.
5. Se houver publicação reescrita, combinar a substituição e a atualização dos clones; exigir novo clone ou procedimento de recuperação documentado. Revisar tags/releases/forks separadamente. Coordenar proteção de branches e avisar sobre invalidação de hashes.

**Nenhuma reescrita de histórico, force-push, revogação ou emissão foi executada.** São ações administrativas externas que exigem credenciais e autorização do proprietário. Não basta rodar filter-repo para considerar o segredo safe.

## Dados locais

- Não versionar `data/`, sessões, chaves, cofres, certificados, logs com conteúdo de mensagens nem bancos de produção.
- Não enviar credenciais em conversa. `--doctor` não deve exibir `api_hash`, telefone, tokens nem conteúdo de mensagens.
- Antes de compartilhar diagnóstico, revisar e redigir o texto; um erro de biblioteca pode conter identificadores.
- Permissões efetivas de sessão/cofre e dependências são verificadas na auditoria, não presumidas a partir do nome do arquivo.
