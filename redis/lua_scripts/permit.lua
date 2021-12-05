local function splits(s, delimiter)
    local result = {};
    for match in (s..delimiter):gmatch("(.-)"..delimiter) do
        table.insert(result, match);
    end
    return result;
end


local function assert_permit(key)
    local values = redis.call('get', key) -- Region known limits
    local max_wait = 0
    for i, limit_raw in pairs(splits(values, ',')) do
        -- redis.log(redis.LOG_WARNING, 'Permit: '..limit_raw..' Key: '..key)
        local limit = splits(limit_raw, ':')
        -- These are each limits max and interval, e.g. 500:10
        local max = tonumber(limit[1])
        local interval = limit[2]
        -- Test if current bucket exists
        local bucket_count = tonumber(redis.call('hget', key..':'..limit_raw, 'count')) -- Limit
        if (bucket_count and bucket_count ~= nil) then
            local bucket_rollover = tonumber(redis.call('get', key..':'..limit_raw..':'..'rollover')) -- Limit rollover
            if not (bucket_rollover or bucket_rollover == nil) then bucket_rollover = 0 end
            local ttl = redis.call('pttl', key..':'..limit_raw) -- Limit
            if max <= bucket_count + bucket_rollover then
                if max_wait < ttl then
                    max_wait = ttl
                end
            end
        end
    end
    return max_wait;
end

local function register_request(key, request_time)
    local values = redis.call('get', key) -- Region known limits
    for i, limit_raw in pairs(splits(values, ',')) do
        --redis.log(redis.LOG_WARNING, 'Request: '..limit_raw..' Key: '..key)
        local limit = splits(limit_raw, ':')
        -- These are each limits max and interval, e.g. 500:10
        local max = limit[1]
        local interval = limit[2]
        local second = tostring(math.floor(tonumber(request_time) / 1000))

        if redis.call('exists', key..':'..limit_raw) == 0 then -- Limit
            local old_inflight = redis.call('get', key..':'..limit_raw..':inflight') -- Limit inflight
            redis.call('set', key..':'..limit_raw..':inflight', '0')
            if not old_inflight or old_inflight == nil then old_inflight = '0' end
            redis.call('set', key..':'..limit_raw..':rollover', old_inflight) -- Limit rollover
            redis.call('hsetnx', key..':'..limit_raw, 'count', '0') -- Limit: Init count at 0
            redis.call('hsetnx', key..':'..limit_raw, 'start', request_time) -- Limit: Set start time
            redis.call('expire', key..':'..limit_raw..':inflight', 60 * 60 * 6) -- Limit inflight: set auto-cleanup
            redis.call('expire', key..':'..limit_raw..':rollover', 60 * 60) -- Limit rollover: set auto-cleanup
            redis.call('pexpireat', key..':'..limit_raw, tonumber(request_time) + 1000 * interval) -- Limit: Set TTL

            redis.call('setnx', key..':'..limit_raw..':bucket_init:'..second, 1)
            redis.call('expire', key..':'..limit_raw..':bucket_init:'..second, 60 * 60)

        end
        redis.call('hincrby', key..':'..limit_raw, 'count', '1') -- Limit: Increase counter
        redis.call('incr', key..':'..limit_raw..':inflight') -- Limit inflight: Increase counter
        -- Tracking
        redis.call('incr', key..':'..limit_raw..':tracking:'..second)
        redis.call('expire', key..':'..limit_raw..':tracking:'..second, 60 * 60)
    end
end


local key_zone = KEYS[1]
local key_server = KEYS[2]
local request_time = ARGV[1]
local zone_wait = assert_permit(key_zone)
local server_wait = assert_permit(key_server)
local max = zone_wait
if server_wait > max then max = server_wait end
if max > 0 then
    return max
end

register_request(key_zone, request_time)
register_request(key_server, request_time)
return 0
