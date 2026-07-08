"""
Response formatter for WhatsApp messages.

Converts extraction JSON into human-readable WhatsApp text.
Contains NO business logic — only formatting.
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class ResponseFormatter:
    """
    Transforms an extraction result dict into a human-readable WhatsApp message.

    This class is stateless and performs no I/O.
    """

    def format(self, data: dict) -> str:
        """
        Format extraction JSON as a readable WhatsApp message.

        Args:
            data: The dict returned by ExtractionService.process().

        Returns:
            A formatted string ready to be sent as a WhatsApp message.
        """
        lines: list[str] = ["📄 *Document processed successfully*", ""]

        if not data:
            lines.append("_No data could be extracted from this document._")
            return "\n".join(lines)

        doc_type = data.get("document_type") or data.get("tipo") or data.get("type")
        if doc_type:
            lines.append(f"*Type:* {doc_type}")

        # Field mapping: check common Portuguese and English keys
        field_map = [
            ("sender",        ["remetente", "sender", "pagador", "emitente"]),
            ("receiver",      ["destinatario", "receiver", "recebedor", "favorecido"]),
            ("amount",        ["valor", "amount", "total", "value"]),
            ("date",          ["data", "date", "data_pagamento", "data_emissao"]),
            ("description",   ["descricao", "description", "historico", "observacao"]),
            ("invoice_number",["numero_nota", "numero_nfe", "invoice_number", "chave_acesso"]),
            ("cnpj",          ["cnpj", "cpf", "documento"]),
            ("bank",          ["banco", "bank", "instituicao"]),
            ("key",           ["chave_pix", "chave", "key"]),
            ("transaction_id",["id_transacao", "transaction_id", "id_pagamento"]),
        ]

        displayed_keys: set[str] = set()
        if doc_type:
            displayed_keys.update(["document_type", "tipo", "type"])

        for label, candidates in field_map:
            for key in candidates:
                if key in data and key not in displayed_keys:
                    value = data[key]
                    if value is not None and str(value).strip():
                        lines.append(f"*{label.replace('_', ' ').title()}:* {value}")
                        displayed_keys.update(candidates)
                    break

        # Append any remaining unknown fields
        remaining = {k: v for k, v in data.items() if k not in displayed_keys}
        if remaining:
            lines.append("")
            for key, value in remaining.items():
                if value is not None and str(value).strip():
                    label = key.replace("_", " ").title()
                    lines.append(f"*{label}:* {value}")

        return "\n".join(lines)
