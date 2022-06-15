local function check_limits(key, timestamp)
    -- Get permission to register a new request
    local max_time = 0
    if redis.call('exists', key) then
        local duration = tonumber(redis.call('hget', key, 'duration'))
        local max = tonumber(redis.call('hget', key, 'max'))
        local requests_key = key..':requests'
        -- drop timeouts
        redis.call('zremrangebyscore', requests_key, 0, timestamp)
        -- count remaining
        local current_block = redis.call('zcount', requests_key)
        -- check against limit
        if current_block >= max then
            return tonumber(redis.call('zrange', requests_key, 0, 1, 'WITHSCORES')[2])
        end
    else
        redis.call('hset', key, 'duration', 10, 'max', 100)
    end
    return 0
end


local function update_limits(key, request_id, timestamp)
    -- Register a new request
    local duration = tonumber(redis.call('hget', key, 'duration'))
    local max = tonumber(redis.call('hget', key, 'max'))
    local requests_key = key..':requests'

    redis.call('zadd', requests_key, timestamp + duration + 1, request_id)
    end
end


local timestamp = ARGV[1]
local request_id = ARGV[2]


local server = KEYS[1]
local endpoint = server..':'..KEYS[2]
local wait_until = check_limits(server, timestamp)
wait_until = math.max(wait_until, check_limits(endpoint, timestamp))

if wait_until > 0 then
    return wait_until
end

update_limits(server, request_id, timestamp)
update_limits(endpoint, request_id, timestamp)

