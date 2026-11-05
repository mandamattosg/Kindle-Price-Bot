<p align="center">
  <img src="assets/logo.png" alt="Kindle Bot" width="260">
</p>

# Kindle Price Bot

Bot do Telegram que monitora o preço dos Kindles na Amazon Brasil e avisa os usuários quando o preço cai.

Teste aqui: https://t.me/kindlebymands_bot

## Sobre esta versão

Este repositório é uma **versão enxuta e pública** do projeto, mantida apenas para **fins de portfólio e consulta**.

Algumas partes do código original **não estão incluídas de propósito**, por serem internas ao funcionamento do serviço — entre elas o módulo de coleta de preços (scraper), scripts de infraestrutura e deploy, utilitários de banco de dados e a documentação interna. Por isso, **o projeto não roda sozinho a partir deste repositório**: o `telegram_bot.py`, por exemplo, depende de um módulo que não está aqui.

Também não fazem parte deste repositório credenciais, variáveis de ambiente, sessões ou dados de usuários.

## O que está aqui

| Arquivo | Descrição |
|---|---|
| `telegram_bot.py` | Bot do Telegram: comandos dos usuários (`/start`, `/kindle`, `/status`, `/remover`, ...) e do administrador |
| `database.py` | Acesso ao banco de dados PostgreSQL |
| `alerts.py` | Montagem e envio dos alertas de preço |
| `notification_queue.py` | Fila de notificações |
| `notification_worker.py` | Worker que processa a fila e envia as mensagens |
| `notifications.py` | Avisos para o administrador |
| `promotion_approval.py` | Fluxo de aprovação de promoções pelo administrador |
| `requirements.txt` | Dependências Python |

## Tecnologias

Python · python-telegram-bot · PostgreSQL (psycopg / SQLAlchemy)

## Licença e uso

**Todos os direitos reservados © Amanda Mattos Gontijo.**

Este repositório **não possui licença de código aberto**. O código está disponível publicamente apenas para visualização. **Não é permitido copiar, modificar, redistribuir ou usar este código**, no todo ou em parte, para qualquer finalidade, sem autorização prévia e por escrito da autora.
