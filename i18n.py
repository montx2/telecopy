"""
i18n.py — Internacionalização PT-BR / EN.

Uso:
    from i18n import t, set_lang, get_lang
    t("copy.title")                 → "COPIAR CANAL"
    t("auth.code", n=1)             → "Código de verificação (tentativa 1/3)"

Chaves ausentes retornam a própria chave (nunca quebram a interface).
"""

from __future__ import annotations

from typing import Dict, Tuple

_STRINGS: Dict[str, Dict[str, str]] = {}


def _s(key: str, pt: str, en: str) -> None:
    _STRINGS[key] = {"pt": pt, "en": en}


# ── Geral ─────────────────────────────────────────────────────────────────────
_s("app.tagline", "Replicador de Canais Telegram", "Telegram Channel Replicator")
_s("app.tech", "SQLite WAL  ·  AES-256-GCM  ·  Bloom Filter  ·  Async",
   "SQLite WAL  ·  AES-256-GCM  ·  Bloom Filter  ·  Async")
_s("app.by", "por", "by")
_s("app.ready", "Pronto", "Ready")

# ── Idioma ────────────────────────────────────────────────────────────────────
_s("lang.title", "IDIOMA / LANGUAGE", "LANGUAGE / IDIOMA")
_s("lang.welcome", "Bem-vindo! / Welcome!", "Welcome! / Bem-vindo!")
_s("lang.select", "Selecione o idioma / Select language", "Select language / Selecione o idioma")
_s("lang.pt", "Português (Brasil)", "Portuguese (Brazil)")
_s("lang.en", "English", "English")
_s("lang.current", "Idioma atual", "Current language")
_s("lang.set", "Idioma atualizado.", "Language updated.")

# ── Onboarding ────────────────────────────────────────────────────────────────
_s("onboard.title", "BEM-VINDO AO TELECOPY", "WELCOME TO TELECOPY")
_s("onboard.subtitle", "3 passos rápidos e você já está copiando canais",
   "3 quick steps and you're copying channels")
_s("onboard.step1.title", "Obtenha as credenciais do Telegram", "Get your Telegram credentials")
_s("onboard.step1.body",
   "Acesse https://my.telegram.org/apps, entre com sua conta, clique em 'API development tools' e crie um App. Copie o API ID (número) e o API Hash (32 caracteres).",
   "Go to https://my.telegram.org/apps, sign in, click 'API development tools' and create an App. Copy the API ID (number) and API Hash (32 characters).")
_s("onboard.step2.title", "Cole as credenciais no TELECOPY", "Paste the credentials into TELECOPY")
_s("onboard.step2.body",
   "Na tela seguinte informe API ID e API Hash. Eles são gravados localmente com AES-256-GCM e nunca saem do seu computador.",
   "On the next screen enter the API ID and API Hash. They are stored locally with AES-256-GCM and never leave your computer.")
_s("onboard.step3.title", "Faça login uma única vez", "Sign in once")
_s("onboard.step3.body",
   "Informe seu telefone com DDI (+55 para Brasil). O Telegram informa onde enviou o código: pode ser no chat oficial em outro aparelho conectado, por SMS ou outro método. Se houver senha de dois fatores, ela será pedida em seguida. Depois disso a sessão fica salva.",
   "Enter your phone with country code (+1 for USA). Telegram tells you where it sent the code: it may be the official chat on another signed-in device, SMS, or another method. If 2-step verification is on, you'll be asked for the password. The session is saved afterwards.")
_s("onboard.tip", "Dica: para copiar você precisa apenas SER MEMBRO da origem e ADMINISTRADOR no destino.",
   "Tip: to copy you only need to be a MEMBER of the source and an ADMIN of the destination.")
_s("onboard.continue", "Pressione ENTER para configurar agora", "Press ENTER to set up now")

# ── Autenticação ──────────────────────────────────────────────────────────────
_s("auth.first_time.title", "CONFIGURAÇÃO INICIAL", "INITIAL SETUP")
_s("auth.first_time.subtitle", "Credenciais criptografadas com AES-256-GCM e armazenadas só no seu PC.",
   "Credentials encrypted with AES-256-GCM and stored only on your PC.")
_s("auth.creds_guide.title", "Como obter suas credenciais:", "How to get your credentials:")
_s("auth.creds_guide.steps",
   "Acesse https://my.telegram.org/apps|Faça login com seu número de telefone|Clique em 'API development tools'|Crie um App (qualquer nome)|Copie 'App api_id' e 'App api_hash'",
   "Go to https://my.telegram.org/apps|Sign in with your phone number|Click 'API development tools'|Create an App (any name)|Copy 'App api_id' and 'App api_hash'")
_s("auth.api_id", "API ID (somente números)", "API ID (numbers only)")
_s("auth.api_id_invalid", "API ID inválido: informe apenas números.", "Invalid API ID: numbers only.")
_s("auth.api_hash", "API Hash (32 caracteres)", "API Hash (32 characters)")
_s("auth.api_hash_invalid", "API Hash inválido: deve ter 32 caracteres.", "Invalid API Hash: must be 32 characters.")
_s("auth.creds_saved", "Credenciais salvas com criptografia AES-256-GCM.", "Credentials saved with AES-256-GCM.")
_s("auth.conn_failed", "Não foi possível conectar ao Telegram", "Could not connect to Telegram")
_s("auth.connecting", "Conectando ao Telegram…", "Connecting to Telegram…")
_s("auth.conn_hint", "Verifique sua internet, firewall ou proxy e tente novamente.",
   "Check your internet connection, firewall or proxy and try again.")
_s("auth.try_again", "Tentar conectar novamente?", "Try connecting again?")
_s("auth.conn_timeout", "tempo esgotado — rede bloqueando a conexão",
   "timed out — the network is blocking the connection")
_s("auth.signed_in", "Conectado como", "Signed in as")
_s("auth.session_invalid", "Sessão inválida ou expirada — será necessário entrar novamente.",
   "Session invalid or expired — you'll need to sign in again.")
_s("auth.sign_in.title", "VERIFICAÇÃO DE CONTA", "ACCOUNT VERIFICATION")
_s("auth.sign_in.subtitle", "O método de entrega do código depende do Telegram.",
   "Telegram chooses how the login code is delivered.")
_s("auth.phone", "Telefone com DDI (ex.: +5511999999999)", "Phone with country code (e.g. +15550000000)")
_s("auth.phone_invalid", "Informe um número internacional válido, começando por + e com 8 a 15 dígitos (ex.: +5511999999999).",
   "Enter a valid international number, starting with + and containing 8 to 15 digits (e.g. +15550000000).")
_s("auth.sending_code", "Solicitando código ao Telegram…", "Requesting a code from Telegram…")
_s("auth.request_accepted", "Solicitação aceita pelo Telegram. Veja abaixo onde procurar o código.",
   "Telegram accepted the request. See below where to look for the code.")
_s("auth.code_delivery_app", "Confira o chat oficial 'Telegram' em outro aparelho/sessão onde sua conta já está conectada. Não é SMS e o código não aparece nesta janela.",
   "Check the official 'Telegram' chat on another device/session where your account is already signed in. This is not SMS, and the code will not appear in this window.")
_s("auth.code_delivery_sms", "Confira as mensagens SMS do número da conta, inclusive spam/bloqueadas.",
   "Check SMS messages for the account number, including spam/blocked messages.")
_s("auth.code_delivery_call", "O Telegram indicou uma ligação telefônica. Atenda o número associado à conta.",
   "Telegram indicated a phone call. Answer the call to the account number.")
_s("auth.code_delivery_email", "Confira o e-mail associado à conta e a pasta de spam.",
   "Check the email associated with the account and its spam folder.")
_s("auth.code_delivery_other", "Confira as notificações e todos os aparelhos em que a conta já está conectada; o Telegram pode usar outro método de entrega.",
   "Check notifications and every device where the account is signed in; Telegram may use another delivery method.")
_s("auth.code_hint", "Se nada chegar, não solicite códigos repetidamente: confira a conta/número e aguarde possíveis limites temporários do Telegram.",
   "If nothing arrives, do not repeatedly request codes: verify the account/number and wait in case Telegram applied a temporary limit.")
_s("auth.code_send_fail", "Não foi possível enviar o código", "Could not send the code")
_s("auth.code_send_timeout", "O Telegram demorou para responder. Verifique a conexão e tente novamente mais tarde.",
   "Telegram did not respond in time. Check the connection and try again later.")
_s("auth.code_api_invalid", "O Telegram rejeitou o API ID/Hash. Confira os dois valores em my.telegram.org/apps.",
   "Telegram rejected the API ID/Hash. Check both values at my.telegram.org/apps.")
_s("auth.code_phone_banned", "O Telegram não permite login com esse número. Verifique o status da conta pelo app oficial ou fale com o suporte do Telegram.",
   "Telegram does not allow login with this number. Check the account in the official app or contact Telegram support.")
_s("auth.code_flood", "O Telegram limitou temporariamente as tentativas. Não peça outro código até o tempo indicado passar.",
   "Telegram temporarily limited login attempts. Do not request another code until the indicated time has passed.")
_s("auth.code", "Código (tentativa {n}/3)", "Code (attempt {n}/3)")
_s("auth.code_wrong", "Código incorreto. Tente novamente.", "Wrong code. Try again.")
_s("auth.code_expired", "Código expirado — solicitando um novo método/código…", "Code expired — requesting a new code…")
_s("auth.2fa_hint", "Sua conta tem verificação em duas etapas (2FA).", "Your account has two-step verification (2FA).")
_s("auth.2fa", "Senha da verificação em duas etapas", "Two-step verification password")
_s("auth.2fa_fail", "Senha 2FA incorreta", "Wrong 2FA password")
_s("auth.too_many", "Muitas tentativas. Encerrando por segurança.", "Too many attempts. Exiting for safety.")
_s("auth.fail", "Falha na autenticação.", "Authentication failed.")
_s("auth.flood", "O Telegram pediu uma pausa de {sec}s antes de tentar novamente.",
   "Telegram asked to wait {sec}s before trying again.")
_s("auth.credentials_recovered", "O cofre existe, mas não pôde ser lido. Informe as credenciais novamente; elas serão salvas em: {path}",
   "The credentials vault exists but could not be read. Enter credentials again; they will be saved to: {path}")
_s("auth.credentials_location", "Credenciais salvas e verificadas em: {path}",
   "Credentials saved and verified at: {path}")
_s("auth.credentials_save_fail", "Não foi possível confirmar a gravação das credenciais em {path}: {reason}",
   "Could not verify saved credentials at {path}: {reason}")

# ── Menu ──────────────────────────────────────────────────────────────────────
_s("menu.copy", "Copiar canal", "Copy channel")
_s("menu.copy.desc", "Clonar todo o histórico de um canal", "Clone a channel's full history")
_s("menu.multi", "Multi-destino", "Multi-destination")
_s("menu.multi.desc", "Copiar para vários canais de uma vez", "Copy to several channels at once")
_s("menu.mirror", "Modo espelho", "Mirror mode")
_s("menu.mirror.desc", "Sincronizar novas mensagens em tempo real", "Sync new messages in real time")
_s("menu.clone", "Clone automático", "Automatic clone")
_s("menu.clone.desc", "Criar o destino e copiar tudo em um passo", "Create the destination and copy everything in one step")
_s("menu.tools", "Ferramentas", "Tools")
_s("menu.tools.desc", "Diagnóstico, estatísticas e manutenção", "Diagnostics, statistics and maintenance")
_s("menu.settings", "Configurações", "Settings")
_s("menu.settings.desc", "Preferências, idioma e segurança", "Preferences, language and security")
_s("menu.exit", "Sair", "Exit")
_s("menu.back", "Voltar", "Back")
_s("menu.option", "Opção", "Option")
_s("menu.invalid", "Opção inválida.", "Invalid option.")
_s("menu.footer", "ENTER para confirmar  ·  0 para voltar", "ENTER to confirm  ·  0 to go back")

# ── Onboarding do seletor ─────────────────────────────────────────────────────
_s("picker.title", "SELECIONAR CANAL", "SELECT CHANNEL")
_s("picker.loading", "Carregando seus canais e grupos", "Loading your channels and groups")
_s("picker.found", "canais encontrados", "channels found")
_s("picker.none", "Nenhum canal ou grupo encontrado nesta conta.",
   "No channels or groups found in this account.")
_s("picker.page", "página", "page")
_s("picker.prev", "anterior", "previous")
_s("picker.next", "próxima", "next")
_s("picker.search_btn", "buscar", "search")
_s("picker.auto_create_btn", "criar destino", "create destination")
_s("picker.auto_create_empty", "Nenhum chat elegível encontrado. Criar um destino automaticamente?", "No eligible chat found. Create a destination automatically?")
_s("picker.search", "busca", "search")
_s("picker.search_prompt", "Buscar por nome", "Search by name")
_s("picker.select", "Número do canal", "Channel number")
_s("picker.invalid", "Número inválido.", "Invalid number.")
_s("picker.from_list", "Escolher da lista", "Pick from list")
_s("picker.from_list.desc", "Seus canais e grupos, com busca", "Your channels and groups, with search")
_s("picker.manual", "Digitar manualmente", "Type manually")
_s("picker.manual.desc", "@usuario, link t.me/… ou ID numérico", "@username, t.me/… link or numeric ID")
_s("picker.how", "Como deseja informar", "How do you want to specify")
_s("picker.forum_warn", "Este grupo é um FÓRUM (com tópicos).", "This group is a FORUM (with topics).")
_s("picker.topic_prompt", "ID ou link do tópico da origem (ENTER = todos os tópicos)", "Source topic ID or link (ENTER = all topics)")
_s("picker.topic_prompt_dest", "ID ou link do tópico do destino (ENTER = recriar toda a estrutura de tópicos da origem)", "Destination topic ID or link (ENTER = recreate the whole source topic structure)")
_s("picker.topic_full_hint", "ENTER nos dois prompts de tópico = recriar toda a estrutura de tópicos da origem.", "ENTER on both topic prompts = recreate the whole source topic structure.")
_s("picker.topic_invalid", "Formato inválido. Cole o link de uma mensagem do tópico.", "Invalid format. Paste a message link from the topic.")
_s("picker.topic_all", "Todos os tópicos", "All topics")
_s("picker.source_hl", "← ORIGEM", "← SOURCE")
_s("picker.type_channel", "Canal", "Channel")
_s("picker.type_group", "Grupo", "Group")
_s("picker.type_forum", "Fórum", "Forum")
_s("picker.header_num", "#", "#")
_s("picker.header_type", "Tipo", "Type")
_s("picker.header_id", "ID", "ID")
_s("picker.header_members", "Membros", "Members")
_s("picker.header_name", "Nome", "Name")

# ── Entidades ─────────────────────────────────────────────────────────────────
_s("entity.source", "Origem", "Source")
_s("entity.dest", "Destino", "Destination")
_s("entity.name", "Nome", "Name")
_s("entity.id", "ID", "ID")
_s("entity.type", "Tipo", "Type")
_s("entity.members", "Membros", "Members")
_s("entity.username", "Usuário", "Username")
_s("entity.source_hint", "De onde as mensagens serão copiadas", "Where messages will be copied from")
_s("entity.dest_hint", "Para onde as mensagens serão enviadas", "Where messages will be sent to")
_s("entity.not_found", "Não encontrado. Verifique se você é membro e se o link/ID está correto.",
   "Not found. Check that you're a member and the link/ID is correct.")
_s("entity.same", "Origem e destino são o mesmo canal. Escolha outro destino.",
   "Source and destination are the same channel. Pick another destination.")

# ── Cópia ─────────────────────────────────────────────────────────────────────
_s("copy.title", "COPIAR CANAL", "COPY CHANNEL")
_s("copy.dest.mode", "Como deseja preparar o destino?", "How do you want to set up the destination?")
_s("copy.dest.existing", "Usar um destino existente", "Use an existing destination")
_s("copy.dest.existing.desc", "Selecionar um canal ou grupo que já existe", "Pick a channel or group that already exists")
_s("copy.dest.create", "Criar destino automaticamente", "Create the destination automatically")
_s("copy.dest.create.desc", "Criar um canal/grupo privado com a mesma estrutura da origem", "Create a private channel/group with the source structure")
_s("copy.clone.title_prompt", "Título do novo destino (ENTER = {title})", "New destination title (ENTER = {title})")
_s("copy.clone.title_invalid", "O título precisa ter de 1 a 128 caracteres.", "The title must contain 1 to 128 characters.")
_s("copy.clone.title_empty", "O título do novo destino não pode ficar vazio.", "The new destination title cannot be empty.")
_s("copy.clone.title_length", "O título tem {count} caracteres; o limite do Telegram é {max}.", "The title has {count} characters; Telegram's limit is {max}.")
_s("copy.clone.topic_read_failed", "Não foi possível ler a estrutura do fórum de origem: {reason}", "Could not read the source forum structure: {reason}")
_s("copy.clone.topic_count_mismatch", "O Telegram informou {count} tópicos, mas só foram lidos {found}.", "Telegram reported {count} topics, but only {found} were read.")
_s("copy.clone.topic_pagination_stuck", "A paginação dos tópicos não avançou.", "Topic pagination did not advance.")
_s("copy.clone.topic_page_limit", "A lista de tópicos excedeu o limite de páginas.", "The topic list exceeded the page limit.")
_s("copy.clone.created_data_missing", "O Telegram criou o destino, mas não retornou os dados do canal.", "Telegram created the destination but did not return its channel data.")
_s("copy.clone.topic_id_missing", "O Telegram não retornou o ID do tópico {topic}.", "Telegram did not return the ID for topic {topic}.")
_s("copy.clone.forums_required", "A origem e o destino precisam ser fóruns.", "The source and destination must both be forums.")
_s("copy.clone.destination_forum_required", "Para dividir a cópia em tópicos, o destino também precisa ser um fórum. Ative tópicos no grupo de destino ou crie um destino automaticamente.", "To split the copy into topics, the destination must also be a forum. Enable topics in the destination group or create a destination automatically.")
_s("copy.clone.topic_missing_destination", "O destino não contém o tópico {topic}.", "The destination does not contain the topic {topic}.")
_s("copy.clone.topic_ambiguous", "Há mais de um tópico chamado {topic} no destino; não é seguro escolher automaticamente.", "The destination has multiple topics named {topic}; choosing automatically would be unsafe.")
_s("copy.clone.map_topics", "Tópicos de origem e destino associados por nome/ícone.", "Source and destination topics matched by name/icon.")
_s("copy.clone.new", "Será criado automaticamente", "Will be created automatically")
_s("copy.clone.kind.channel", "Canal de transmissão", "Broadcast channel")
_s("copy.clone.kind.group", "Grupo", "Group")
_s("copy.clone.kind.forum", "Fórum com tópicos", "Forum with topics")
_s("copy.clone.topics", "Tópicos que serão recriados", "Topics to recreate")
_s("copy.clone.private_note", "O novo destino será privado. O Telegram não permite duplicar username, membros ou permissões.", "The new destination will be private. Telegram does not allow cloning usernames, members or permissions.")
_s("copy.clone.about_warning", "A descrição da origem não pôde ser copiada: {reason}", "The source description could not be copied: {reason}")
_s("copy.clone.creating", "Criando o novo destino e os tópicos no Telegram…", "Creating the new destination and its Telegram topics…")
_s("copy.clone.flood_wait", "O Telegram pediu uma pausa de {seconds}s; aguardando antes de continuar…", "Telegram requested a {seconds}s pause; waiting before continuing…")
_s("copy.clone.created", "Destino criado: {title} (ID {id})", "Destination created: {title} (ID {id})")
_s("copy.clone.failed", "Não foi possível criar o destino: {reason}", "Could not create the destination: {reason}")
_s("copy.clone.partial", "O Telegram já criou o destino (ID {id}), mas a estrutura ficou incompleta. Ele continua na sua conta; confira antes de tentar novamente.", "Telegram created the destination (ID {id}), but its structure is incomplete. It remains in your account; inspect it before retrying.")
_s("copy.clone.setting_failed", "Não foi possível restaurar as opções do tópico {topic}: {reason}", "Could not restore settings for topic {topic}: {reason}")
_s("copy.clone.existing_topics", "Tópicos já existentes que serão reutilizados", "Existing topics that will be reused")
_s("copy.clone.missing_topics", "Tópicos ausentes que serão criados no destino", "Missing topics that will be created in the destination")
_s("copy.clone.preparing_existing", "Conferindo e completando os tópicos do destino existente…", "Checking and completing the existing destination topics…")
_s("copy.clone.ready_existing", "Estrutura de tópicos pronta no destino existente.", "Topic structure ready in the existing destination.")
_s("copy.clone.settings_postponed", "A cópia não ficou completa; opções como fechar/ocultar tópicos não foram aplicadas para permitir a retomada.", "The copy is not complete; options such as closing/hiding topics were not applied so you can resume.")
_s("copy.clone.faithful_forum", "Cópia de fórum: repetições entre tópicos serão preservadas; deduplicação e assinatura ficam desativadas nesta cópia.", "Forum copy: repetitions across topics are preserved; deduplication and signature are disabled for this copy.")
_s("copy.clone.destination_not_forum", "O destino não está com os tópicos ativados (não é um fórum).", "The destination does not have topics enabled (it is not a forum).")
_s("copy.clone.enable_forum_prompt", "Ativar os tópicos no destino agora e recriar a estrutura da origem? Você precisa ser administrador do grupo.", "Enable topics in the destination now and recreate the source structure? You must be a group admin.")
_s("copy.clone.enable_forum_needs_admin", "Sua conta não é administradora do destino, então não é possível ativar os tópicos.", "Your account is not an admin of the destination, so topics cannot be enabled.")
_s("copy.clone.enable_forum_failed", "Não foi possível ativar os tópicos no destino: {reason}", "Could not enable topics in the destination: {reason}")
_s("copy.clone.enable_forum_done", "Tópicos ativados no destino.", "Topics enabled in the destination.")
_s("copy.clone.sequential_copy", "Destino sem tópicos: as mensagens serão copiadas em sequência, sem estrutura de fórum.", "Destination without topics: messages will be copied in sequence, without a forum structure.")
_s("copy.clone.single_topic_warn", "A origem é um FÓRUM INTEIRO e você escolheu um único tópico no destino: TODAS as mensagens irão para esse tópico e nenhum tópico será recriado.", "The source is a WHOLE FORUM and you picked a single destination topic: ALL messages will go to that topic and no topic will be recreated.")
_s("copy.clone.single_topic_confirm", "Continuar enviando tudo para esse tópico?", "Continue sending everything to that topic?")
_s("copy.clone.topic_icon_fallback", "O Telegram recusou o ícone do tópico {topic}; ele foi criado com a cor original, sem o emoji.", "Telegram rejected the icon of topic {topic}; it was created with the original color, without the emoji.")
_s("copy.clone.topic_icon_dropped", "O Telegram recusou o emoji e a cor do tópico {topic}; ele foi criado sem ícone.", "Telegram rejected both the emoji and the color of topic {topic}; it was created without an icon.")
_s("copy.clone.legacy_checkpoints", "Há {count} checkpoint(s) antigo(s) deste par origem→destino de uma execução sem tópicos. Retomar vai PULAR essas mensagens, que ficaram no General.", "There are {count} old checkpoint(s) for this source→destination pair from a run without topics. Resuming will SKIP those messages, which stayed in General.")
_s("copy.clone.legacy_reset_prompt", "Zerar os checkpoints apenas deste par para copiar tudo de novo (recomendado)?", "Clear the checkpoints of this pair only so everything is copied again (recommended)?")
_s("copy.clone.legacy_reset_done", "{count} registro(s) deste par foram apagados.", "{count} record(s) for this pair were deleted.")
_s("copy.clone.legacy_reset_failed", "Não foi possível zerar os checkpoints deste par: {reason}", "Could not clear the checkpoints of this pair: {reason}")
# ── Clone automático ─────────────────────────────────────────────────────────
_s("copy.clone.kind", "Tipo do destino", "Destination type")
_s("clone.intro", "Escolha só a origem: o destino é criado automaticamente com o mesmo tipo, título e tópicos.", "Pick only the source: the destination is created automatically with the same type, title and topics.")
_s("clone.summary", "CLONE AUTOMÁTICO", "AUTOMATIC CLONE")
_s("clone.confirm", "Criar o destino e iniciar a cópia agora?", "Create the destination and start copying now?")
_s("clone.photo", "Copiar foto do perfil", "Copy profile photo")
_s("clone.start_copy", "Copiar após criar", "Copy after creating")
_s("clone.suffix", "Sufixo do título", "Title suffix")
_s("clone.photo_none", "A origem não tem foto de perfil; nada a copiar.", "The source has no profile photo; nothing to copy.")
_s("clone.photo_done", "Foto do perfil copiada para o destino.", "Profile photo copied to the destination.")
_s("clone.photo_failed", "A foto do perfil não pôde ser copiada (a cópia continua): {reason}", "The profile photo could not be copied (the copy continues): {reason}")
_s("clone.noforwards_done", "Proteção de conteúdo (sem encaminhamento) aplicada ao destino.", "Content protection (no forwarding) applied to the destination.")
_s("clone.noforwards_failed", "Não foi possível aplicar a proteção de conteúdo no destino: {reason}", "Could not apply content protection to the destination: {reason}")
_s("clone.partial", "O destino {title} (ID {id}) foi criado, mas a estrutura ficou incompleta.", "Destination {title} (ID {id}) was created, but its structure is incomplete.")
_s("clone.partial_continue", "Continuar no destino criado", "Continue in the created destination")
_s("clone.partial_continue.desc", "Completa os tópicos que faltam e copia", "Completes the missing topics and copies")
_s("clone.partial_delete", "Excluir o destino criado", "Delete the created destination")
_s("clone.partial_delete.desc", "Apaga do Telegram apenas o canal criado agora", "Deletes only the channel created now from Telegram")
_s("clone.partial_delete_confirm", "Excluir definitivamente o canal {title} criado por esta execução? Só ele será apagado.", "Permanently delete the channel {title} created by this run? Only it will be deleted.")
_s("clone.deleted", "Destino excluído: {title}", "Destination deleted: {title}")
_s("clone.delete_failed", "Não foi possível excluir o destino criado: {reason}", "Could not delete the created destination: {reason}")
_s("clone.err_too_much", "Sua conta atingiu o limite de canais/grupos do Telegram. Use um destino existente (Copiar canal → 1).", "Your account reached Telegram's channel/group limit. Use an existing destination (Copy channel → 1).")
_s("clone.err_flood", "O Telegram limitou as criações por enquanto. Aguarde alguns minutos e tente de novo.", "Telegram rate-limited creations for now. Wait a few minutes and try again.")
_s("clone.err_admin", "Sua conta precisa ser administradora para concluir esta etapa.", "Your account must be an admin to finish this step.")
_s("clone.reuse_found", "Já existe um clone desta origem: {title} (ID {id}).", "A clone of this source already exists: {title} (ID {id}).")
_s("clone.reuse_btn", "Reutilizar o clone", "Reuse the clone")
_s("clone.reuse_desc", "Continua/sincroniza a cópia no destino existente", "Continues/syncs the copy in the existing destination")
_s("clone.new_btn", "Criar outro destino", "Create another destination")
_s("clone.new_desc", "Cria um canal novo a partir da origem", "Creates a new channel from the source")
_s("clone.reuse_missing", "O clone registrado (ID {id}) não existe mais; o registro foi descartado.", "The recorded clone (ID {id}) no longer exists; the record was discarded.")
_s("clone.reuse_no_admin", "Você não é administrador de {title}; esse clone não pode ser reutilizado.", "You are not an admin of {title}; that clone cannot be reused.")
_s("clone.structure_only", "Destino pronto: {title} (ID {id}). A cópia não foi iniciada (Configurações → iniciar cópia).", "Destination ready: {title} (ID {id}). The copy was not started (Settings → start copy).")
_s("clone.no_destination", "Nenhum destino disponível; nada foi copiado.", "No destination available; nothing was copied.")
_s("clone.done_hint", "Clone concluído. O destino (ID {id}) é privado; o Telegram não copia username, membros nem permissões.", "Clone finished. The destination (ID {id}) is private; Telegram does not copy username, members or permissions.")
_s("settings.clone_suffix.option", "Sufixo do título do clone", "Clone title suffix")
_s("settings.clone_suffix.hint", "O sufixo é anexado ao título da origem (o espaço antes dele é automático). Digite - para não usar sufixo.", "The suffix is appended to the source title (the space before it is automatic). Type - to use no suffix.")
_s("settings.clone_suffix.title", "Sufixo (atual: {current})", "Suffix (current: {current})")
_s("settings.clone_suffix.set", "Sufixo atualizado: {val}", "Suffix updated: {val}")
_s("settings.clone_suffix.too_long", "Esse sufixo tem {count} caracteres; use no máximo {max} para caber no título do Telegram (128).", "That suffix has {count} characters; use at most {max} so it fits Telegram's title limit (128).")
_s("clone.reuse_failed", "Não foi possível reutilizar o clone: {reason}", "Could not reuse the clone: {reason}")
_s("clone.reuse_only", "Clone existente pronto: {title} (ID {id}). A cópia não foi iniciada (Configurações → iniciar cópia).", "Existing clone ready: {title} (ID {id}). The copy was not started (Settings → start copy).")
_s("settings.clone_photo.toggle", "Copiar foto ao criar clone", "Copy photo when creating a clone")
_s("settings.clone_start.toggle", "Iniciar cópia após criar", "Start copying after creating")

_s("copy.topic_unmapped", "A mensagem pertence ao tópico de origem {topic}, que não está no mapa do destino. Atualize a estrutura antes de copiar.", "The message belongs to source topic {topic}, which is not mapped in the destination. Refresh the structure before copying.")
_s("copy.unsupported_media", "Tipo de mensagem não reproduzível pela API ({kind}); registrado como falha, não como cópia concluída.", "Message type cannot be reproduced by the API ({kind}); recorded as a failure, not as a completed copy.")
_s("copy.download_unavailable", "Download da mídia indisponível ou expirado.", "Media download unavailable or expired.")
_s("copy.download_failed", "Não foi possível baixar a mídia: {reason}", "Could not download the media: {reason}")
_s("order.violation", "Violação de ordem detectada; refaça o tópico/destino afetado.",
   "Order violation detected; rebuild the affected topic/destination.")
_s("placeholder.pending", "⚠️ Conteúdo em recuperação (mensagem {id}). Motivo: {reason}",
   "⚠️ Content being recovered (message {id}). Reason: {reason}")
_s("placeholder.unrecoverable", "❌ Conteúdo indisponível (mensagem {id}). Motivo: {reason}",
   "❌ Content unavailable (message {id}). Reason: {reason}")
_s("placeholder.missing", "A mensagem original não está mais disponível na origem.",
   "The original message is no longer available in the source.")
_s("placeholder.continuation", "⚠️ Continuação pendente da mensagem {id} (parte {part}).",
   "⚠️ Pending continuation of message {id} (part {part}).")
_s("settings.placeholder", "Reservar posição em falhas", "Reserve position on failures")
_s("settings.placeholder.toggle", "Reservar posição em falhas (recomendado)",
   "Reserve position on failures (recommended)")
_s("tools.recover", "Recuperar lacunas", "Recover gaps")
_s("tools.recover.desc", "Preenche marcadores por edição, mantendo a ordem",
   "Fill placeholders by editing, keeping the order")
_s("tools.recover.title", "RECUPERAR LACUNAS", "RECOVER GAPS")
_s("tools.recover.empty", "Nenhuma reserva aberta para recuperar.",
   "No open placeholders to recover.")
_s("tools.recover.confirm", "Recuperar agora as reservas abertas?",
   "Recover the open placeholders now?")
_s("tools.recover.done", "Recuperação concluída.", "Recovery finished.")
_s("tools.recover.columns", "Par origem→destino: abertas / recuperadas / irrecuperáveis",
   "Source→destination pair: open / recovered / unrecoverable")
_s("tools.recover.pair", "Origem {source} → destino {dest}",
   "Source {source} → destination {dest}")
_s("tools.recover.failed", "Falhas na recuperação", "Recovery failures")
_s("copy.placeholders", "Reservas de posição: {count} (recupere em [5]→6)",
   "Position placeholders: {count} (recover in [5]→6)")
_s("result.placeholders", "Reservas de posição", "Position placeholders")
_s("result.placeholders.hint", "recupere em [5]→6", "recover in [5]→6")
_s("result.unrecoverable", "Irrecuperáveis", "Unrecoverable")
_s("result.pending_placeholders",
   "Conferência: há reservas de posição pendentes; a cópia NÃO está completa. Use [5]→6.",
   "Check: there are pending position placeholders; the copy is NOT complete. Use [5]→6.")
_s("copy.send_unconfirmed", "O Telegram não confirmou o ID da mensagem enviada; o item não foi marcado como concluído.", "Telegram did not confirm the sent message ID; the item was not marked as completed.")
_s("result.complete", "Conferência: todas as mensagens selecionadas foram concluídas.", "Check: all selected messages were completed.")
_s("result.incomplete", "Conferência: a cópia NÃO está completa. Veja erros/tópicos abaixo e retome.", "Check: the copy is NOT complete. See errors/topics below and resume.")
_s("result.topics", "CONFERÊNCIA POR TÓPICO", "PER-TOPIC CHECK")
_s("result.topic_header", "Tópico", "Topic")
_s("result.done_header", "Concluídas", "Completed")
_s("result.expected_header", "Esperadas", "Expected")
_s("copy.source_not_found", "Origem não encontrada", "Source not found")
_s("copy.dest_not_found", "Destino não encontrado", "Destination not found")
_s("copy.summary", "RESUMO DA CÓPIA", "COPY SUMMARY")
_s("copy.resume", "Retomar de onde parou (pula o que já foi enviado)?",
   "Resume where it stopped (skip what was already sent)?")
_s("copy.confirm", "Iniciar a cópia agora?", "Start copying now?")
_s("copy.cancelled", "Cancelado pelo usuário.", "Cancelled by the user.")
_s("copy.indexing", "Lendo mensagens da origem", "Reading messages from the source")
_s("copy.indexed", "mensagens lidas", "messages read")
_s("copy.found", "mensagens encontradas", "messages found")
_s("copy.none", "Nenhuma mensagem encontrada com esse filtro.", "No messages found with this filter.")
_s("copy.all_done", "Tudo já foi copiado antes — nada a fazer.", "Everything was already copied — nothing to do.")
_s("copy.already", "já estavam copiadas (checkpoint)", "were already copied (checkpoint)")
_s("copy.albums", "álbuns detectados", "albums detected")
_s("copy.sent", "enviadas", "sent")
_s("copy.skipped", "ignoradas", "skipped")
_s("copy.errors", "erros", "errors")
_s("copy.duplicates", "duplicatas evitadas", "duplicates avoided")
_s("copy.starting", "Iniciando transferência…", "Starting transfer…")
_s("copy.interrupted", "Interrompido — o progresso foi salvo e pode ser retomado.",
   "Interrupted — progress saved and can be resumed.")
_s("copy.options", "Opções desta cópia", "Options for this copy")
_s("copy.mode", "Modo de envio", "Send mode")
_s("copy.mode.copy", "Reenviar (cópia limpa, sem 'encaminhado de')", "Re-send (clean copy, no 'forwarded from')")
_s("copy.mode.forward", "Encaminhar (mais rápido, mantém 'encaminhado de')", "Forward (faster, keeps 'forwarded from')")
_s("copy.mode.hint", "Reenviar consome mais tempo e banda, mas remove a origem.",
   "Re-sending takes more time and bandwidth but removes the origin label.")
_s("copy.first_failure", "Primeira falha no envio: {reason}", "First send failure: {reason}")
_s("copy.prepare_failure", "Falha ao preparar uma mensagem para envio. Confira os logs para mais detalhes.",
   "A message could not be prepared for sending. Check the logs for details.")
_s("copy.stop_repeated", "Cópia pausada para não continuar acumulando falhas. Corrija o motivo e tente novamente.",
   "Copy paused to avoid accumulating more failures. Fix the cause and try again.")
_s("copy.stop_fatal", "Cópia interrompida: essa falha bloqueia o envio. Confira a origem, o destino e suas permissões antes de tentar novamente.",
   "Copy stopped: this error blocks sending. Check the source, destination and your permissions before trying again.")
_s("copy.stop_flood", "Cópia pausada após um limite temporário do Telegram. Aguarde o tempo indicado antes de tentar novamente.",
   "Copy paused after a temporary Telegram limit. Wait the indicated time before trying again.")
_s("copy.source_protected", "Canal com restrição de encaminhamento: usando modo de reenvio (download + upload).",
   "Channel with forwarding restriction: using re-send mode (download + upload).")
_s("copy.limit_reached", "Limite diário de {limit} mensagens atingido. Aguarde amanhã ou ajuste em Configurações.",
   "Daily limit of {limit} messages reached. Wait until tomorrow or change it in Settings.")
_s("copy.limit_warn", "Atenção: {count}/{limit} mensagens hoje.", "Warning: {count}/{limit} messages today.")

# ── Multi-destino ─────────────────────────────────────────────────────────────
_s("multi.title", "MULTI-DESTINO", "MULTI-DESTINATION")
_s("multi.dest_n", "Destino #{n} (ENTER para começar)", "Destination #{n} (ENTER to start)")
_s("multi.added", "Adicionado", "Added")
_s("multi.already", "já está na lista", "already in the list")
_s("multi.list", "Destinos selecionados", "Selected destinations")
_s("multi.no_dests", "Nenhum destino adicionado.", "No destinations added.")
_s("multi.confirm", "Copiar para {n} destino(s)?", "Copy to {n} destination(s)?")
_s("multi.dest_label", "Destino {i}/{total}", "Destination {i}/{total}")
_s("multi.failed", "Falhou", "Failed")
_s("multi.start", "Começar", "Start")
_s("multi.remove", "Remover último", "Remove last")
_s("multi.total_sent", "Total enviado em {n} destino(s)", "Total sent across {n} destination(s)")
_s("multi.topic", "tópico {id}", "topic {id}")
_s("multi.forum_full", "fórum: estrutura de tópicos completa", "forum: full topic structure")
_s("multi.skipped_topic", "ignorado: envio para um único tópico não confirmado", "skipped: sending to a single topic was not confirmed")

# ── Modo espelho ──────────────────────────────────────────────────────────────
_s("mirror.title", "MODO ESPELHO", "MIRROR MODE")
_s("mirror.waiting", "MODO ESPELHO ATIVO — aguardando mensagens novas (Ctrl+C para sair)",
   "MIRROR MODE ON — waiting for new messages (Ctrl+C to exit)")
_s("mirror.stop", "Mantenha esta janela aberta. Tudo que chegar na origem será copiado.",
   "Keep this window open. Anything that arrives at the source is copied.")
_s("mirror.new_msg", "mensagem copiada (ID {id})", "message copied (ID {id})")
_s("mirror.stopped", "Modo espelho encerrado.", "Mirror mode stopped.")
_s("mirror.sent", "enviadas nesta sessão", "sent in this session")
_s("mirror.topic_created", "tópico {topic} criado no destino", "topic {topic} created in the destination")

# ── Resultados ────────────────────────────────────────────────────────────────
_s("result.title", "RESULTADO", "RESULT")
_s("result.sent", "Enviadas", "Sent")
_s("result.skipped", "Ignoradas", "Skipped")
_s("result.errors", "Erros", "Errors")
_s("result.duplicates", "Duplicatas evitadas", "Duplicates avoided")
_s("result.albums", "Álbuns", "Albums")
_s("result.time", "Tempo total", "Total time")
_s("result.speed", "Velocidade", "Speed")
_s("result.bytes", "Transferido", "Transferred")
_s("result.reason", "Detalhe do erro", "Error details")
_s("result.per_min", "msg/min", "msg/min")

# ── Ferramentas ───────────────────────────────────────────────────────────────
_s("tools.title", "FERRAMENTAS", "TOOLS")
_s("tools.resolve", "Consultar canal", "Look up channel")
_s("tools.resolve.desc", "Ver nome, ID e tipo de um canal/link", "Show name, ID and type of a channel/link")
_s("tools.resolve.title", "CONSULTAR CANAL", "LOOK UP CHANNEL")
_s("tools.resolve.hint", "Digite @usuario, link t.me/… ou ID. ENTER em branco para sair.",
   "Type @username, t.me/… link or ID. Blank ENTER to exit.")
_s("tools.resolve.query", "Canal", "Channel")
_s("tools.resolve.result", "DADOS DO CANAL", "CHANNEL DATA")
_s("tools.resolve.not_found", "Não encontrado", "Not found")
_s("tools.resolve.name", "Nome", "Name")
_s("tools.resolve.type", "Tipo", "Type")
_s("tools.resolve.access", "Acesso", "Access")
_s("tools.resolve.private", "Privado", "Private")
_s("tools.resolve.public", "Público", "Public")
_s("tools.stats", "Estatísticas", "Statistics")
_s("tools.stats.desc", "Números de uso e limite diário", "Usage numbers and daily limit")
_s("tools.stats.title", "ESTATÍSTICAS", "STATISTICS")
_s("tools.stats.session", "ESTA INSTALAÇÃO", "THIS INSTALLATION")
_s("tools.stats.alltime", "HISTÓRICO DE CÓPIAS", "COPY HISTORY")
_s("tools.stats.daily", "HOJE", "TODAY")
_s("tools.stats.total", "Total", "Total")
_s("tools.stats.photos", "Fotos", "Photos")
_s("tools.stats.videos", "Vídeos", "Videos")
_s("tools.stats.audio", "Áudios", "Audio")
_s("tools.stats.documents", "Documentos", "Documents")
_s("tools.stats.text", "Textos", "Text")
_s("tools.stats.bytes", "Enviado", "Sent")
_s("tools.stats.sessions", "Sessões", "Sessions")
_s("tools.stats.duplicates", "Duplicatas", "Duplicates")
_s("tools.stats.errors", "Erros", "Errors")
_s("tools.stats.moved", "Dados", "Data")
_s("tools.stats.today", "Enviadas hoje", "Sent today")
_s("tools.stats.limit", "Limite diário", "Daily limit")
_s("tools.stats.remaining", "Restantes", "Remaining")
_s("tools.stats.recent", "ÚLTIMAS CÓPIAS", "LATEST COPIES")
_s("tools.stats.last_run", "Última execução", "Last run")
_s("tools.retry", "Fila de repetição", "Retry queue")
_s("tools.retry.desc", "Mensagens que falharam e podem ser reenviadas", "Messages that failed and can be re-sent")
_s("tools.retry.title", "FILA DE REPETIÇÃO", "RETRY QUEUE")
_s("tools.retry.queue", "PENDENTES", "PENDING")
_s("tools.retry.pending", "Aguardando", "Waiting")
_s("tools.retry.total", "Total", "Total")
_s("tools.retry.attempts", "Tentativas somadas", "Total attempts")
_s("tools.retry.empty", "Fila vazia — nenhuma mensagem pendente.", "Queue empty — no pending messages.")
_s("tools.retry.retry_now", "Tentar reenviar agora?", "Try re-sending now?")
_s("tools.retry.clear", "Limpar a fila?", "Clear the queue?")
_s("tools.retry.cleared", "itens removidos", "items removed")
_s("tools.retry.done", "reprocessadas", "reprocessed")
_s("tools.dedup", "Deduplicação", "Deduplication")
_s("tools.dedup.desc", "Ver estado das camadas de proteção", "Inspect the protection layers")
_s("tools.dedup.title", "DEDUPLICAÇÃO", "DEDUPLICATION")
_s("tools.dedup.lru", "CACHE LRU", "LRU CACHE")
_s("tools.dedup.bloom", "BLOOM FILTER", "BLOOM FILTER")
_s("tools.dedup.db", "BANCO DE DADOS", "DATABASE")
_s("tools.dedup.items", "Itens", "Items")
_s("tools.dedup.usage", "Uso", "Usage")
_s("tools.dedup.hashes", "Funções hash", "Hash functions")
_s("tools.dedup.memory", "Memória", "Memory")
_s("tools.dedup.fp", "Falsos positivos", "False positives")
_s("tools.dedup.history", "Hashes guardados", "Stored hashes")
_s("tools.dedup.size", "Tamanho do banco", "Database size")
_s("tools.dedup.reset", "Limpar cache em memória?", "Clear in-memory cache?")
_s("tools.dedup.reset_done", "Cache limpo (o banco permanece intacto).", "Cache cleared (database untouched).")
_s("tools.maint", "Manutenção", "Maintenance")
_s("tools.maint.desc", "Limpar temporários, otimizar banco e backup", "Clean temp files, optimize database and back up")
_s("tools.maint.title", "MANUTENÇÃO", "MAINTENANCE")
_s("tools.maint.tmp_found", "arquivos temporários", "temporary files")
_s("tools.maint.tmp_delete", "Apagar os arquivos temporários?", "Delete temporary files?")
_s("tools.maint.tmp_cleared", "Temporários removidos", "Temp files removed")
_s("tools.maint.tmp_none", "Nenhum arquivo temporário.", "No temporary files.")
_s("tools.maint.db_prune", "Remover histórico com mais de 30 dias?", "Remove history older than 30 days?")
_s("tools.maint.db_done", "registros antigos removidos", "old records removed")
_s("tools.maint.db_vacuum", "Compactar o banco de dados (VACUUM)?", "Compact the database (VACUUM)?")
_s("tools.maint.db_vacuum_done", "Banco compactado.", "Database compacted.")
_s("tools.maint.backup", "Criar backup do banco agora?", "Create a database backup now?")
_s("tools.maint.backup_done", "Backup criado em", "Backup created at")
_s("tools.maint.health", "SAÚDE DO SISTEMA", "SYSTEM HEALTH")
_s("tools.maint.integrity", "Integridade do banco", "Database integrity")
_s("tools.maint.folders", "Pastas de dados", "Data folders")

# ── Configurações ─────────────────────────────────────────────────────────────
_s("settings.title", "CONFIGURAÇÕES", "SETTINGS")
_s("settings.current", "CONFIGURAÇÃO ATUAL", "CURRENT SETTINGS")
_s("settings.delay", "Intervalo entre envios", "Delay between sends")
_s("settings.workers", "Workers de download", "Download workers")
_s("settings.dedup", "Deduplicação", "Deduplication")
_s("settings.resume", "Retomar automaticamente", "Auto-resume")
_s("settings.forward", "Modo de envio", "Send mode")
_s("settings.signature", "Assinatura", "Signature")
_s("settings.on", "ativado ✓", "on ✓")
_s("settings.off", "desativado ✗", "off ✗")
_s("settings.delay.option", "Intervalo entre envios", "Send delay")
_s("settings.delay.hint", "Mínimo 0,1 s. Recomendado 0,9 s (evita bloqueio do Telegram).",
   "Minimum 0.1 s. Recommended 0.9 s (avoids Telegram flood blocks).")
_s("settings.delay.title", "Novo intervalo em segundos (atual: {current}s)", "New delay in seconds (current: {current}s)")
_s("settings.delay.set", "Intervalo definido para {val}s", "Delay set to {val}s")
_s("settings.delay.invalid", "Número inválido.", "Invalid number.")
_s("settings.dedup.toggle", "Alternar deduplicação", "Toggle deduplication")
_s("settings.workers.option", "Workers (paralelismo)", "Workers (parallelism)")
_s("settings.workers.hint", "Mais workers = mais rápido. Recomendado para o seu PC: {rec}.",
   "More workers = faster. Recommended for your PC: {rec}.")
_s("settings.workers.set", "Workers definidos: {val}", "Workers set to {val}")
_s("settings.workers.title", "Número de workers (1-{max}, atual: {current})", "Workers (1-{max}, current: {current})")
_s("settings.forward.toggle", "Alternar modo de envio", "Toggle send mode")
_s("settings.forward.set", "Modo de envio: {val}", "Send mode: {val}")
_s("settings.resume.toggle", "Alternar retomada automática", "Toggle auto-resume")
_s("settings.signature.option", "Assinatura no fim das mensagens", "Signature at the end of messages")
_s("settings.signature.hint", "Deixe em branco para remover. Ex.: @meucanal",
   "Leave blank to remove. E.g. @mychannel")
_s("settings.signature.set", "Assinatura definida.", "Signature set.")
_s("settings.logs.export", "Exportar logs", "Export logs")
_s("settings.logs.desc", "Salvar o log em data/exports/", "Save the log to data/exports/")
_s("settings.logs.export_confirm", "Exportar o log agora?", "Export the log now?")
_s("settings.logs.none", "Nenhum log encontrado.", "No log file found.")
_s("settings.logs.done", "Exportado para", "Exported to")
_s("settings.creds.reset", "Redefinir credenciais", "Reset credentials")
_s("settings.creds.desc", "Apagar credenciais e sessão do Telegram", "Delete credentials and Telegram session")
_s("settings.creds.confirm", "Isso apaga as credenciais salvas e a sessão atual. Você precisará configurar tudo de novo.",
   "This deletes saved credentials and the current session. You'll have to set everything up again.")
_s("settings.creds.confirm_short", "Confirmar reset de credenciais?", "Confirm credential reset?")
_s("settings.creds.cleared", "Credenciais removidas. O programa será encerrado.", "Credentials cleared. The app will exit.")
_s("settings.lang", "Idioma / Language", "Language / Idioma")
_s("settings.lang.desc", "Mudar o idioma da interface", "Change the interface language")
_s("settings.lang.title", "IDIOMA", "LANGUAGE")
_s("settings.lang.current", "atual", "current")
_s("settings.lang.set", "Idioma atualizado.", "Language updated.")
_s("settings.about", "Sobre o TELECOPY", "About TELECOPY")
_s("settings.status", "ESTADO ATUAL", "CURRENT STATE")
_s("settings.data_reset", "Apagar histórico de cópias", "Clear copy history")
_s("settings.data_reset.desc", "Remove checkpoints e deduplicação (não apaga credenciais)",
   "Removes checkpoints and dedup data (keeps credentials)")
_s("settings.data_reset.confirm", "Isso fará o programa copiar tudo novamente. Confirmar?",
   "This will make the app copy everything again. Confirm?")
_s("settings.data_reset.done", "Histórico de cópias apagado.", "Copy history cleared.")

# ── Sobre ─────────────────────────────────────────────────────────────────────
_s("about.title", "SOBRE", "ABOUT")
_s("about.version", "Versão", "Version")
_s("about.author", "Autor", "Author")
_s("about.license", "Licença", "License")
_s("about.arch", "Arquitetura", "Architecture")
_s("about.arch.val", "Async · SQLite WAL · AES-256-GCM", "Async · SQLite WAL · AES-256-GCM")
_s("about.dedup", "Deduplicação", "Deduplication")
_s("about.dedup.val", "LRU + Bloom Filter + SQLite", "LRU + Bloom Filter + SQLite")
_s("about.system", "Sistema", "System")
_s("about.python", "Python", "Python")
_s("about.data_dir", "Pasta de dados", "Data folder")
_s("about.site", "Repositório", "Repository")

# ── Progresso ─────────────────────────────────────────────────────────────────
_s("progress.copying", "Copiando", "Copying")
_s("progress.downloading", "Baixando", "Downloading")
_s("progress.uploading", "Enviando", "Uploading")
_s("progress.flood", "Telegram pediu pausa de {sec}s (anti-flood)", "Telegram asked to wait {sec}s (anti-flood)")
_s("progress.download_fail", "Falha no download", "Download failed")
_s("progress.upload_fail", "Falha no envio", "Upload failed")
_s("progress.retry", "Tentativa {n}/{max}", "Attempt {n}/{max}")
_s("progress.eta", "restam", "left")
_s("progress.speed", "velocidade", "speed")
_s("progress.done", "Concluído", "Done")
_s("progress.waiting", "Aguardando", "Waiting")

# ── Retomada / fila ───────────────────────────────────────────────────────────
_s("queue.retry_start", "Reprocessando mensagens que falharam…", "Re-processing failed messages…")
_s("queue.nothing", "Nada para reprocessar.", "Nothing to re-process.")

# ── Limites ───────────────────────────────────────────────────────────────────
_s("limit.reached", "Limite diário atingido: {count}/{limit} mensagens hoje.", "Daily limit reached: {count}/{limit} messages today.")
_s("limit.warn", "Atenção: {count}/{limit} mensagens hoje ({remaining} restantes).", "Warning: {count}/{limit} messages today ({remaining} left).")

# ── Prompts genéricos ─────────────────────────────────────────────────────────
_s("prompt.continue", "Pressione ENTER para continuar", "Press ENTER to continue")
_s("prompt.required", "Este campo é obrigatório.", "This field is required.")
_s("prompt.yes_no", "[s/N]", "[y/N]")
_s("prompt.yes_chars", "s sim y yes", "y yes s sim")
_s("prompt.no_chars", "n nao não no", "n no nao")

# ── Encerramento ──────────────────────────────────────────────────────────────
_s("shutdown.saving", "Salvando estatísticas…", "Saving statistics…")
_s("shutdown.db", "Fechando banco de dados…", "Closing database…")
_s("shutdown.disc", "Desconectando do Telegram…", "Disconnecting from Telegram…")
_s("shutdown.bye", "Até logo! / Goodbye!", "Goodbye! / Até logo!")

# ── Inicialização ─────────────────────────────────────────────────────────────
_s("init.loading", "Inicializando", "Initializing")
_s("init.checking", "Verificando integridade dos dados", "Checking data integrity")
_s("init.locked", "O TELECOPY já está aberto em outra janela.", "TELECOPY is already running in another window.")
_s("init.recovered", "Cofre de credenciais ilegível neste computador — será necessário entrar novamente.",
   "Credentials vault unreadable on this computer — you'll need to sign in again.")
_s("init.python", "Python {ver}", "Python {ver}")

# ── Filtros ───────────────────────────────────────────────────────────────────
_s("filter.title", "FILTRO DE MENSAGENS", "MESSAGE FILTER")
_s("filter.default", "Filtro", "Filter")
_s("filter.selected", "Filtro aplicado", "Filter applied")
_s("filter.back_hint", "ENTER = todas as mensagens", "ENTER = all messages")
_s("filter.all", "Todas as mensagens", "All messages")
_s("filter.all.desc", "Sem filtro — copia tudo", "No filter — copies everything")
_s("filter.photos", "Somente fotos", "Photos only")
_s("filter.photos.desc", "Imagens enviadas como foto", "Images sent as photos")
_s("filter.videos", "Somente vídeos", "Videos only")
_s("filter.videos.desc", "Arquivos de vídeo", "Video files")
_s("filter.audio", "Somente áudios", "Audio only")
_s("filter.audio.desc", "Músicas, vozes e áudios", "Music, voice notes and audio")
_s("filter.documents", "Somente documentos", "Documents only")
_s("filter.documents.desc", "PDFs, ZIPs e outros arquivos", "PDFs, ZIPs and other files")
_s("filter.text", "Somente textos", "Text only")
_s("filter.text.desc", "Mensagens sem mídia", "Messages without media")
_s("filter.links", "Somente com links", "With links only")
_s("filter.links.desc", "Mensagens que contêm URLs", "Messages containing URLs")
_s("filter.media", "Somente mídia", "Media only")
_s("filter.media.desc", "Fotos, vídeos, áudios e documentos", "Photos, videos, audio and documents")
_s("filter.large", "Arquivos grandes", "Large files")
_s("filter.large.desc", "Arquivos acima de 50 MB", "Files over 50 MB")
_s("filter.recent", "Últimos 7 dias", "Last 7 days")
_s("filter.recent.desc", "Somente mensagens recentes", "Only recent messages")
_s("filter.original", "Somente originais", "Originals only")
_s("filter.original.desc", "Ignora mensagens encaminhadas", "Ignores forwarded messages")

# ── Erros comuns ──────────────────────────────────────────────────────────────
_s("err.channel_private", "Este canal é privado e você não é membro.", "This channel is private and you're not a member.")
_s("err.no_permission", "Sem permissão para enviar neste destino. Você precisa ser administrador.",
   "No permission to send to this destination. You must be an admin.")
_s("err.generic", "Erro inesperado", "Unexpected error")
_s("err.need_admin", "Você precisa ser administrador no destino e ter permissão para publicar mensagens.",
   "You must be an admin in the destination with permission to post messages.")
_s("err.forward_restricted", "O canal de origem protege o conteúdo e o Telegram bloqueou o encaminhamento. Peça autorização ao responsável pelo canal.",
   "The source channel protects its content and Telegram blocked forwarding. Ask the channel owner for permission.")
_s("err.send_forbidden", "Sua conta não tem permissão para publicar no destino. Confira se é administrador e se pode postar mensagens.",
   "Your account cannot post to the destination. Check that it is an admin and can post messages.")
_s("err.db_busy", "O banco de dados está ocupado. Feche outras instâncias do TELECOPY.",
   "The database is busy. Close other TELECOPY instances.")

_LANG: str = "pt"
_SUPPORTED: Tuple[str, ...] = ("pt", "en")


def set_lang(lang: str) -> None:
    global _LANG
    if lang in _SUPPORTED:
        _LANG = lang


def get_lang() -> str:
    return _LANG


def t(key: str, **kwargs) -> str:
    entry = _STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_LANG) or entry.get("pt") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            pass
    return text


def available_languages() -> Tuple[str, ...]:
    return _SUPPORTED


def all_keys() -> Tuple[str, ...]:
    return tuple(_STRINGS.keys())


def missing(lang: str) -> Tuple[str, ...]:
    return tuple(k for k, v in _STRINGS.items() if not v.get(lang))
