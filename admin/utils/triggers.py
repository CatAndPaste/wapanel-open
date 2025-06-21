import asyncpg


async def init_triggers_pg(settings):
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )

    # instance_change
    #
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

    # msg_change trigger:
    # When message inserted or updated
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION notify_msg_change() RETURNS trigger AS $$
        DECLARE
            row  record;
            act  text;
        BEGIN
            IF (TG_OP = 'DELETE') THEN
                row := OLD; act := 'delete';
            ELSIF (TG_OP = 'UPDATE') THEN
                row := NEW; act := 'update';
            ELSE
                row := NEW; act := 'insert';
            END IF;
        
            PERFORM pg_notify(
                'msg_change',
                json_build_object(
                    'action', act,
                    'id'     , row.id,
                    'inst_id', row.instance_id,
                    'chat_id', row.chat_id
                )::text
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        
        DROP TRIGGER IF EXISTS trg_notify_msg_change ON messages;
        CREATE TRIGGER trg_notify_msg_change
        AFTER INSERT OR UPDATE OR DELETE ON messages
        FOR EACH ROW EXECUTE FUNCTION notify_msg_change();
        """
    )

    # users
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION notify_user_change() RETURNS trigger AS $$
        DECLARE
            payload json;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                payload := json_build_object('action','delete', 'id',OLD.id);
            ELSIF TG_OP = 'UPDATE' THEN
                payload := json_build_object('action','update', 'id',NEW.id);
            ELSE
                payload := json_build_object('action','insert', 'id',NEW.id);
            END IF;
            PERFORM pg_notify('user_change', payload::text);
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        
        DROP TRIGGER IF EXISTS trg_notify_user_change ON users;
        CREATE TRIGGER trg_notify_user_change
        AFTER INSERT OR UPDATE OR DELETE ON users
        FOR EACH ROW EXECUTE FUNCTION notify_user_change();
        """
    )
    await conn.close()