import asyncpg


async def init_triggers_pg(settings):
    """
    Creates or ensures that DB triggers are present:
    1. instance changes insert/update/delete
    2. incoming db messages insert
    3. outgoing db messages (status == pending) insert
    """
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )

    # instance_change trigger:
    # instance insert, update or delete. Used for Green API Client Manager
    # for delete we also send api_id
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION notify_instance_change() RETURNS trigger AS $$
        DECLARE payload json;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                payload := json_build_object('action','delete', 'id',OLD.id, 'api_id',OLD.api_id);
            ELSIF TG_OP = 'UPDATE' THEN
                payload := json_build_object('action','update', 'id',NEW.id);
            ELSE
                payload := json_build_object('action','insert', 'id',NEW.id);
            END IF;
            PERFORM pg_notify('instance_change',payload::text);
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_notify_instance_change ON instances;
        CREATE TRIGGER trg_notify_instance_change
        AFTER INSERT OR UPDATE OR DELETE ON instances
        FOR EACH ROW EXECUTE FUNCTION notify_instance_change();
        """
    )

    # msg_in trigger:
    # New System / Incoming Message (insert only) -> used for telegram channel notifications
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION notify_msg_in() RETURNS trigger AS $$
        DECLARE payload json;
        BEGIN
            IF (NEW.direction = 'sys') OR (NEW.direction = 'inc') THEN
                payload := json_build_object('msg_id', NEW.id);
                PERFORM pg_notify('msg_in',payload::text);
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_notify_msg_in ON messages;
        CREATE TRIGGER trg_notify_msg_in
        AFTER INSERT ON messages
        FOR EACH ROW EXECUTE FUNCTION notify_msg_in();
        """
    )

    # msg_out trigger:
    # new outgoing message created with status == pending, we send it through Green API
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION notify_msg_out() RETURNS trigger AS $$
        DECLARE payload json;
        BEGIN
            IF (NEW.direction = 'out') AND (NEW.status = 'pending') THEN
                payload := json_build_object('msg_id', NEW.id);
                PERFORM pg_notify('msg_out', payload::text);
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_notify_msg_out ON messages;
        CREATE TRIGGER trg_notify_msg_out
        AFTER INSERT ON messages
        FOR EACH ROW EXECUTE FUNCTION notify_msg_out();
        """
    )

    await conn.close()
