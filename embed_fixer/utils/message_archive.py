from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from tortoise.connection import get_connection

if TYPE_CHECKING:
    import discord

    from embed_fixer.cogs.fixer import Media


MESSAGE_ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS message
(
    channel_id TEXT NOT NULL,
    id TEXT NOT NULL,
    content TEXT NOT NULL,
    username TEXT NOT NULL,
    "timestamp" INTEGER NOT NULL DEFAULT (unixepoch()),
    CONSTRAINT message_pk PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS "message_id_IDX" ON "message" ("id");
CREATE TABLE IF NOT EXISTS embed
(
    message_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author_icon_url TEXT,
    author_name TEXT,
    author_url TEXT,
    description TEXT NOT NULL,
    footer_icon_url TEXT,
    footer_text TEXT,
    image_url TEXT,
    image_proxy_url TEXT,
    "timestamp" TEXT NOT NULL,
    local_filename TEXT,
    orginal_url TEXT,
    is_downloaded INTEGER NOT NULL DEFAULT (0)
);
CREATE TABLE IF NOT EXISTS attachment
(
    id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    "size" INTEGER NOT NULL,
    filename TEXT NOT NULL,
    url TEXT NOT NULL,
    ephemeral INTEGER NOT NULL,
    is_downloaded INTEGER NOT NULL DEFAULT (0),
    CONSTRAINT attachment_pk PRIMARY KEY (id)
);
"""


async def init_message_archive_schema() -> None:
    db = get_connection("default")
    if db.capabilities.dialect != "sqlite":
        logger.warning("Message archive schema is only created for SQLite connections")
        return

    execute_script = getattr(db, "execute_script", None)
    if execute_script is not None:
        await execute_script(MESSAGE_ARCHIVE_SCHEMA)
        return

    for statement in MESSAGE_ARCHIVE_SCHEMA.split(";"):
        statement = statement.strip()
        if statement:
            await db.execute_query(f"{statement};")


async def archive_message(
    message: discord.Message,
    *,
    url_pairs: list[tuple[str, str]] | None = None,
    medias: list[Media] | None = None,
) -> None:
    try:
        db = get_connection("default")
        if db.capabilities.dialect != "sqlite":
            return

        await _archive_message(message, url_pairs=url_pairs or [], medias=medias or [])
    except Exception:
        logger.exception(
            f"Failed to archive message {message.id} in channel {message.channel.id}"
        )


async def _archive_message(
    message: discord.Message,
    *,
    url_pairs: list[tuple[str, str]],
    medias: list[Media],
) -> None:
    db = get_connection("default")
    message_id = str(message.id)
    timestamp = int(message.created_at.timestamp())
    embed_timestamp = message.created_at.isoformat()

    await db.execute_query(
        """
        INSERT OR REPLACE INTO "message"
            (channel_id, id, content, username, "timestamp")
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            str(message.channel.id),
            message_id,
            message.content,
            message.author.display_name,
            timestamp,
        ],
    )

    await db.execute_query('DELETE FROM "embed" WHERE message_id = ?', [message_id])
    await db.execute_query('DELETE FROM "attachment" WHERE message_id = ?', [message_id])

    attachment_rows = [
        [
            str(attachment.id),
            message_id,
            attachment.size,
            attachment.filename,
            attachment.url,
            int(attachment.ephemeral),
            0,
        ]
        for attachment in message.attachments
    ]
    if attachment_rows:
        await db.execute_many(
            """
            INSERT OR REPLACE INTO "attachment"
                (id, message_id, "size", filename, url, ephemeral, is_downloaded)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            attachment_rows,
        )

    embed_rows: list[list[object | None]] = []
    for embed in message.embeds:
        timestamp_value = embed.timestamp.isoformat() if embed.timestamp else embed_timestamp
        embed_rows.append(
            [
                message_id,
                embed.title or "",
                embed.url or "",
                embed.author.icon_url,
                embed.author.name,
                embed.author.url,
                embed.description or "",
                embed.footer.icon_url,
                embed.footer.text,
                embed.image.url,
                embed.image.proxy_url,
                timestamp_value,
                None,
                None,
                0,
            ]
        )

    for original_url, processed_url in url_pairs:
        embed_rows.append(
            [
                message_id,
                "",
                processed_url,
                None,
                None,
                None,
                "",
                None,
                None,
                None,
                None,
                embed_timestamp,
                None,
                original_url,
                0,
            ]
        )

    for media in medias:
        local_filename = None if media.file is None else media.file.filename
        embed_rows.append(
            [
                message_id,
                "",
                media.url,
                None,
                None,
                None,
                "",
                None,
                None,
                media.url,
                None,
                embed_timestamp,
                local_filename,
                media.url,
                int(media.file is not None),
            ]
        )

    if embed_rows:
        await db.execute_many(
            """
            INSERT INTO "embed"
                (
                    message_id, title, url, author_icon_url, author_name, author_url,
                    description, footer_icon_url, footer_text, image_url, image_proxy_url,
                    "timestamp", local_filename, orginal_url, is_downloaded
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            embed_rows,
        )
