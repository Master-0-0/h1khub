/*
 * h1khub_json — a small RedisJSON-compatible module for the H1KHub replica.
 *
 * Commands: JSON.SET / JSON.GET / JSON.DEL / JSON.TYPE / JSON.OBJKEYS /
 *           JSON.ARRAPPEND / JSON.NUMINCRBY / JSON.STRLEN / JSON.TOGGLE /
 *           JSON.CLEAR
 *
 * Values are stored under a module data type ("ReJSON-RL") so TYPE reports the
 * JSON type and RDB/AOF persistence works. Path handling is intentionally
 * root-only ("$" or "."): the replica's Redis browser edits whole documents,
 * not sub-paths.
 *
 * Build: gcc -fPIC -shared -O2 -I<redis-src>/src -o h1khub_json.so h1khub_json.c -lcjson
 */
#include "redismodule.h"
#include <cjson/cJSON.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define JSON_TYPE_NAME "ReJSON-RL"
#define JSON_ENC_VER 1

typedef struct {
    char *json;
    size_t len;
} JsonValue;

static RedisModuleType *JsonType = NULL;

/* ------------------------------------------------------------------ helpers */
static JsonValue *json_value_new(const char *text, size_t len) {
    JsonValue *v = RedisModule_Calloc(1, sizeof(JsonValue));
    v->json = RedisModule_Alloc(len + 1);
    memcpy(v->json, text, len);
    v->json[len] = '\0';
    v->len = len;
    return v;
}

static void json_value_replace(JsonValue *v, const char *text, size_t len) {
    char *fresh = RedisModule_Alloc(len + 1);
    memcpy(fresh, text, len);
    fresh[len] = '\0';
    RedisModule_Free(v->json);
    v->json = fresh;
    v->len = len;
}

static int json_root_path(RedisModuleCtx *ctx, RedisModuleString *path, size_t *len) {
    const char *s = RedisModule_StringPtrLen(path, len);
    if (*len == 1 && (s[0] == '$' || s[0] == '.')) return 1;
    RedisModule_ReplyWithError(ctx, "ERR only the root path ($ or .) is supported");
    return 0;
}

static JsonValue *json_fetch(RedisModuleKey *key) {
    if (RedisModule_KeyType(key) != REDISMODULE_KEYTYPE_MODULE) return NULL;
    if (RedisModule_ModuleTypeGetType(key) != JsonType) return NULL;
    return RedisModule_ModuleTypeGetValue(key);
}

static JsonValue *json_fetch_or_reply(RedisModuleCtx *ctx, RedisModuleKey *key) {
    JsonValue *v = json_fetch(key);
    if (!v) RedisModule_ReplyWithError(ctx, "ERR key does not hold a JSON value");
    return v;
}

/* ------------------------------------------------------------- type methods */
static void *JsonRdbLoad(RedisModuleIO *rdb, int encver) {
    if (encver != JSON_ENC_VER) return NULL;
    size_t len = 0;
    char *buf = RedisModule_LoadStringBuffer(rdb, &len);
    if (!buf) return NULL;
    JsonValue *v = json_value_new(buf, len);
    RedisModule_Free(buf);
    return v;
}

static void JsonRdbSave(RedisModuleIO *rdb, void *value) {
    JsonValue *v = value;
    RedisModule_SaveStringBuffer(rdb, v->json, v->len);
}

static void JsonAofRewrite(RedisModuleIO *aof, RedisModuleString *key, void *value) {
    JsonValue *v = value;
    RedisModule_EmitAOF(aof, "JSON.SET", "scb", key, "$", v->json, v->len);
}

static void JsonFree(void *value) {
    JsonValue *v = value;
    if (!v) return;
    RedisModule_Free(v->json);
    RedisModule_Free(v);
}

static size_t JsonMemUsage(const void *value) {
    const JsonValue *v = value;
    return sizeof(JsonValue) + v->len + 1;
}

static void JsonDigest(RedisModuleDigest *digest, void *value) {
    JsonValue *v = value;
    RedisModule_DigestAddStringBuffer(digest, (const char *)v->json, v->len);
    RedisModule_DigestEndSequence(digest);
}

/* --------------------------------------------------------------- JSON.SET */
static int cmd_json_set(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 4 || argc > 5) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (!json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;

    int nx = 0, xx = 0;
    if (argc == 5) {
        const char *opt = RedisModule_StringPtrLen(argv[4], NULL);
        if (!strcasecmp(opt, "NX")) nx = 1;
        else if (!strcasecmp(opt, "XX")) xx = 1;
        else return RedisModule_ReplyWithError(ctx, "ERR syntax: NX or XX");
    }

    size_t len = 0;
    const char *text = RedisModule_StringPtrLen(argv[3], &len);
    cJSON *parsed = cJSON_ParseWithLength(text, len);
    if (!parsed) return RedisModule_ReplyWithError(ctx, "ERR invalid JSON");
    cJSON_Delete(parsed);

    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ | REDISMODULE_WRITE);
    int exists = RedisModule_KeyType(key) != REDISMODULE_KEYTYPE_EMPTY;
    if (nx && exists) {
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR key already exists");
    }
    if (xx && !exists) {
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR key does not exist");
    }
    if (exists && RedisModule_KeyType(key) != REDISMODULE_KEYTYPE_MODULE) {
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "WRONGTYPE Operation against a key holding the wrong kind of value");
    }
    JsonValue *v = json_value_new(text, len);
    RedisModule_ModuleTypeSetValue(key, JsonType, v);
    RedisModule_CloseKey(key);
    RedisModule_ReplicateVerbatim(ctx);
    return RedisModule_ReplyWithSimpleString(ctx, "OK");
}

/* --------------------------------------------------------------- JSON.GET */
static int cmd_json_get(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2 || argc > 3) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (argc == 3 && !json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;

    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ);
    JsonValue *v = json_fetch(key);
    if (!v) {
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithNull(ctx);
    }
    RedisModule_ReplyWithStringBuffer(ctx, v->json, v->len);
    RedisModule_CloseKey(key);
    return REDISMODULE_OK;
}

/* --------------------------------------------------------------- JSON.DEL */
static int cmd_json_del(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2 || argc > 3) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (argc == 3 && !json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ | REDISMODULE_WRITE);
    int exists = RedisModule_KeyType(key) != REDISMODULE_KEYTYPE_EMPTY;
    if (exists) RedisModule_DeleteKey(key);
    RedisModule_CloseKey(key);
    if (exists) RedisModule_ReplicateVerbatim(ctx);
    return RedisModule_ReplyWithLongLong(ctx, exists ? 1 : 0);
}

/* -------------------------------------------------------------- JSON.TYPE */
static int cmd_json_type(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2 || argc > 3) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (argc == 3 && !json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ);
    JsonValue *v = json_fetch(key);
    if (!v) {
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithNull(ctx);
    }
    cJSON *root = cJSON_ParseWithLength(v->json, v->len);
    const char *name = "null";
    if (root) {
        switch (root->type & 0xFF) {
            case cJSON_False: name = "boolean"; break;
            case cJSON_True: name = "boolean"; break;
            case cJSON_NULL: name = "null"; break;
            case cJSON_Number: name = "number"; break;
            case cJSON_String: name = "string"; break;
            case cJSON_Array: name = "array"; break;
            case cJSON_Object: name = "object"; break;
            default: name = "null"; break;
        }
        cJSON_Delete(root);
    }
    RedisModule_ReplyWithSimpleString(ctx, name);
    RedisModule_CloseKey(key);
    return REDISMODULE_OK;
}

/* ------------------------------------------------------------ JSON.OBJKEYS */
static int cmd_json_objkeys(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2 || argc > 3) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (argc == 3 && !json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ);
    JsonValue *v = json_fetch_or_reply(ctx, key);
    if (!v) {
        RedisModule_CloseKey(key);
        return REDISMODULE_OK;
    }
    cJSON *root = cJSON_ParseWithLength(v->json, v->len);
    if (!root || !cJSON_IsObject(root)) {
        if (root) cJSON_Delete(root);
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR value at path is not an object");
    }
    int n = cJSON_GetArraySize(root);
    RedisModule_ReplyWithArray(ctx, n);
    for (int i = 0; i < n; i++) {
        cJSON *item = cJSON_GetArrayItem(root, i);
        RedisModule_ReplyWithCString(ctx, item->string ? item->string : "");
    }
    cJSON_Delete(root);
    RedisModule_CloseKey(key);
    return REDISMODULE_OK;
}

/* ---------------------------------------------------------- JSON.ARRAPPEND */
static int cmd_json_arrappend(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 4) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (!json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ | REDISMODULE_WRITE);
    JsonValue *v = json_fetch_or_reply(ctx, key);
    if (!v) {
        RedisModule_CloseKey(key);
        return REDISMODULE_OK;
    }
    cJSON *root = cJSON_ParseWithLength(v->json, v->len);
    if (!root || !cJSON_IsArray(root)) {
        if (root) cJSON_Delete(root);
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR value at path is not an array");
    }
    for (int i = 3; i < argc; i++) {
        size_t len = 0;
        const char *text = RedisModule_StringPtrLen(argv[i], &len);
        cJSON *item = cJSON_ParseWithLength(text, len);
        if (!item) {
            cJSON_Delete(root);
            RedisModule_CloseKey(key);
            return RedisModule_ReplyWithError(ctx, "ERR invalid JSON in ARRAPPEND value");
        }
        cJSON_AddItemToArray(root, item);
    }
    char *out = cJSON_PrintUnformatted(root);
    int count = cJSON_GetArraySize(root);
    json_value_replace(v, out, strlen(out));
    cJSON_free(out);
    cJSON_Delete(root);
    RedisModule_ReplicateVerbatim(ctx);
    RedisModule_CloseKey(key);
    return RedisModule_ReplyWithLongLong(ctx, count);
}

/* --------------------------------------------------------- JSON.NUMINCRBY */
static int cmd_json_numincrby(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc != 4) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (!json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    double delta = 0;
    if (RedisModule_StringToDouble(argv[3], &delta) != REDISMODULE_OK) {
        return RedisModule_ReplyWithError(ctx, "ERR increment is not a number");
    }
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ | REDISMODULE_WRITE);
    JsonValue *v = json_fetch_or_reply(ctx, key);
    if (!v) {
        RedisModule_CloseKey(key);
        return REDISMODULE_OK;
    }
    cJSON *root = cJSON_ParseWithLength(v->json, v->len);
    if (!root || !cJSON_IsNumber(root)) {
        if (root) cJSON_Delete(root);
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR value at path is not a number");
    }
    double next = root->valuedouble + delta;
    cJSON_SetNumberValue(root, next);
    char *out = cJSON_PrintUnformatted(root);
    json_value_replace(v, out, strlen(out));
    cJSON_free(out);
    cJSON_Delete(root);
    RedisModule_ReplicateVerbatim(ctx);
    RedisModule_CloseKey(key);
    char buf[64];
    snprintf(buf, sizeof(buf), "%.17g", next);
    return RedisModule_ReplyWithCString(ctx, buf);
}

/* ------------------------------------------------------------- JSON.STRLEN */
static int cmd_json_strlen(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2 || argc > 3) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (argc == 3 && !json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ);
    JsonValue *v = json_fetch_or_reply(ctx, key);
    if (!v) {
        RedisModule_CloseKey(key);
        return REDISMODULE_OK;
    }
    cJSON *root = cJSON_ParseWithLength(v->json, v->len);
    if (!root || !cJSON_IsString(root)) {
        if (root) cJSON_Delete(root);
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR value at path is not a string");
    }
    RedisModule_ReplyWithLongLong(ctx, (long long)strlen(root->valuestring));
    cJSON_Delete(root);
    RedisModule_CloseKey(key);
    return REDISMODULE_OK;
}

/* ------------------------------------------------------------- JSON.TOGGLE */
static int cmd_json_toggle(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2 || argc > 3) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (argc == 3 && !json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ | REDISMODULE_WRITE);
    JsonValue *v = json_fetch_or_reply(ctx, key);
    if (!v) {
        RedisModule_CloseKey(key);
        return REDISMODULE_OK;
    }
    cJSON *root = cJSON_ParseWithLength(v->json, v->len);
    if (!root || !cJSON_IsBool(root)) {
        if (root) cJSON_Delete(root);
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR value at path is not a boolean");
    }
    int next = cJSON_IsTrue(root) ? 0 : 1;
    cJSON_Delete(root);
    const char *text = next ? "true" : "false";
    json_value_replace(v, text, strlen(text));
    RedisModule_ReplicateVerbatim(ctx);
    RedisModule_CloseKey(key);
    return RedisModule_ReplyWithLongLong(ctx, next);
}

/* -------------------------------------------------------------- JSON.CLEAR */
static int cmd_json_clear(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2 || argc > 3) return RedisModule_WrongArity(ctx);
    size_t path_len = 0;
    if (argc == 3 && !json_root_path(ctx, argv[2], &path_len)) return REDISMODULE_OK;
    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ | REDISMODULE_WRITE);
    JsonValue *v = json_fetch_or_reply(ctx, key);
    if (!v) {
        RedisModule_CloseKey(key);
        return REDISMODULE_OK;
    }
    cJSON *root = cJSON_ParseWithLength(v->json, v->len);
    if (!root || (!cJSON_IsArray(root) && !cJSON_IsObject(root))) {
        if (root) cJSON_Delete(root);
        RedisModule_CloseKey(key);
        return RedisModule_ReplyWithError(ctx, "ERR value at path is not an array or object");
    }
    int was_array = cJSON_IsArray(root) ? 1 : 0;
    cJSON_Delete(root);
    if (was_array) json_value_replace(v, "[]", 2);
    else json_value_replace(v, "{}", 2);
    RedisModule_ReplicateVerbatim(ctx);
    RedisModule_CloseKey(key);
    return RedisModule_ReplyWithLongLong(ctx, 1);
}

/* ------------------------------------------------------------------ OnLoad */
int RedisModule_OnLoad(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    (void)argv;
    (void)argc;
    if (RedisModule_Init(ctx, "h1khub_json", 1, REDISMODULE_APIVER_1) == REDISMODULE_ERR) return REDISMODULE_ERR;

    RedisModuleTypeMethods tm = {
        .version = REDISMODULE_TYPE_METHOD_VERSION,
        .rdb_load = JsonRdbLoad,
        .rdb_save = JsonRdbSave,
        .aof_rewrite = JsonAofRewrite,
        .mem_usage = JsonMemUsage,
        .free = JsonFree,
        .digest = JsonDigest,
    };
    JsonType = RedisModule_CreateDataType(ctx, JSON_TYPE_NAME, JSON_ENC_VER, &tm);
    if (!JsonType) return REDISMODULE_ERR;

    if (RedisModule_CreateCommand(ctx, "json.set", cmd_json_set, "write deny-oom", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.get", cmd_json_get, "readonly fast", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.del", cmd_json_del, "write", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.type", cmd_json_type, "readonly fast", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.objkeys", cmd_json_objkeys, "readonly", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.arrappend", cmd_json_arrappend, "write deny-oom", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.numincrby", cmd_json_numincrby, "write deny-oom", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.strlen", cmd_json_strlen, "readonly", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.toggle", cmd_json_toggle, "write", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "json.clear", cmd_json_clear, "write", 1, 1, 1) == REDISMODULE_ERR) return REDISMODULE_ERR;

    return REDISMODULE_OK;
}
