import argparse
import secrets
import string
import sys
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# Permite importar a aplicação ao executar:
# python scripts/provision.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.auth.hash import hash_api_key
from app.core.config import get_settings
from app.database.models import Application
from app.database.session import Base, get_engine


ENV_PATH = PROJECT_ROOT / ".env"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Provisiona a estrutura inicial do banco e gerencia aplicações."
        )
    )

    parser.add_argument(
        "--new-app",
        action="store_true",
        help="Cria uma nova aplicação, mesmo que já existam outras.",
    )

    return parser.parse_args()


def generate_api_key(prefix: str = "sk_live_") -> str:
    """Gera uma API key criptograficamente segura."""
    alphabet = string.ascii_letters + string.digits
    random_part = "".join(
        secrets.choice(alphabet)
        for _ in range(64)
    )

    return f"{prefix}{random_part}"


def update_env_variable(name: str, value: str) -> None:
    """Atualiza ou adiciona uma variável no arquivo .env da raiz."""
    if not ENV_PATH.exists():
        raise FileNotFoundError(
            f"Arquivo de configuração não encontrado: {ENV_PATH}"
        )

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    prefix = f"{name}="

    updated = False
    result: list[str] = []

    for line in lines:
        if line.startswith(prefix):
            result.append(f"{name}={value}")
            updated = True
        else:
            result.append(line)

    if not updated:
        if result and result[-1] != "":
            result.append("")

        result.append(f"{name}={value}")

    ENV_PATH.write_text(
        "\n".join(result) + "\n",
        encoding="utf-8",
    )


def validate_database_connection() -> None:
    """Verifica se o PostgreSQL configurado está acessível."""
    engine = get_engine()

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    print("Conexão com o banco confirmada.")


def create_database_structure() -> None:
    """
    Cria tabelas e índices ausentes.

    Não remove tabelas e não apaga dados existentes.
    """
    engine = get_engine()
    Base.metadata.create_all(bind=engine)

    print("Estrutura do banco verificada com sucesso.")


def get_application_count(session: Session) -> int:
    statement = select(func.count()).select_from(Application)
    return int(session.scalar(statement) or 0)


def ask_yes_no(
    message: str,
    default: bool = True,
) -> bool:
    suffix = " [S/n]: " if default else " [s/N]: "
    answer = input(message + suffix).strip().lower()

    if not answer:
        return default

    return answer in {"s", "sim", "y", "yes"}


def create_application(
    session: Session,
    default_name: str,
) -> tuple[Application, str]:
    name = input(
        f"Nome da aplicação [{default_name}]: "
    ).strip() or default_name

    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)

    application = Application(
        name=name,
        api_key_hash=key_hash,
        active=True,
    )

    session.add(application)
    session.commit()
    session.refresh(application)

    return application, raw_key


def show_created_application(
    application: Application,
    raw_key: str,
    env_updated: bool,
) -> None:
    print()
    print("========================================")
    print(" Aplicação criada com sucesso")
    print("========================================")
    print()
    print(f"Nome: {application.name}")
    print(f"Application ID: {application.id}")
    print()
    print("API key:")
    print()
    print(raw_key)
    print()
    print("ATENÇÃO:")
    print("Esta chave será exibida somente agora.")
    print("Guarde-a em um local seguro.")
    print("Somente o HMAC-SHA256 foi armazenado no banco.")

    if env_updated:
        print()
        print(
            "WUZAPI_APPLICATION_ID foi atualizado "
            "automaticamente no arquivo .env."
        )


def provision(force_new_app: bool = False) -> None:
    print("========================================")
    print(" Provisionamento do banco")
    print("========================================")
    print()

    # Carrega e valida as configurações obrigatórias.
    get_settings()

    print("1/3 - Validando conexão...")
    validate_database_connection()

    print()
    print("2/3 - Verificando estrutura...")
    create_database_structure()

    print()
    print("3/3 - Verificando aplicações...")

    engine = get_engine()

    with Session(engine) as session:
        application_count = get_application_count(session)
        is_initial_application = application_count == 0

        if application_count > 0:
            print(
                f"O banco já possui {application_count} "
                "aplicação(ões) cadastrada(s)."
            )

            if not force_new_app:
                print()
                print(
                    "Nenhuma nova aplicação será criada."
                )
                print(
                    "Use --new-app para cadastrar outra aplicação."
                )
                return

            default_name = "Nova aplicação"

        else:
            print("Nenhuma aplicação foi encontrada.")

            if not ask_yes_no(
                "Deseja criar a aplicação inicial?",
                default=True,
            ):
                print()
                print(
                    "Provisionamento concluído "
                    "sem aplicação inicial."
                )
                return

            default_name = "WhatsApp"

        application, raw_key = create_application(
            session=session,
            default_name=default_name,
        )

    env_updated = False

    if is_initial_application:
        update_env_variable(
            "WUZAPI_APPLICATION_ID",
            str(application.id),
        )
        env_updated = True

    show_created_application(
        application=application,
        raw_key=raw_key,
        env_updated=env_updated,
    )


def main() -> None:
    arguments = parse_arguments()
    provision(force_new_app=arguments.new_app)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("Provisionamento cancelado.")
        raise SystemExit(130)
    except FileNotFoundError as exc:
        print()
        print(f"ERRO: {exc}")
        raise SystemExit(1)
    except SQLAlchemyError as exc:
        print()
        print("ERRO ao acessar o banco de dados:")
        print(exc)
        raise SystemExit(1)
    except Exception as exc:
        print()
        print(f"ERRO durante o provisionamento: {exc}")
        raise SystemExit(1)