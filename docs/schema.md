# Database Schema
We currently use Diskcache as our database, this is a fast embedded database for python.
It uses regular python objects to control data, which means schemas can be arbitrary.
For consistency purposes we define below the schemas of our database.

The schema is a typescript-style definition.

## Common Types
```ts
type UUID = string // e.g. '46a25f86-90d8-41f6-9f06-8ebd99ce0db5'
```

## Users (`store/db/users`)
Key: `Username`
```ts
{
  exchanges: {
    [exchange: string]: string[]
  },
  bots: Set<UUID>,
  strats: Set<UUID>,
  pass: string // Hashed
}
```

## Strategies (`store/db/strats`)
Key: `Strategy UUID`
```ts
{
  uuid: UUID, // Same as Key
  name: string,
  proto: string, // Base Strategy (defined in code)
  // Adjustable parameters; User-defined
  params: {
    [parameter: string]: string | number
  }
}

OR if typeof key != uuid

Strategy
```

## TTL/TimeToLive (`store/db/ttl`)
Key: `TTL UUID`
```ts
{
  expires: number, // time (UTC) at which ttl expires/runs
  files?: string[],
  lambda?: Closure[]
}
```

## Bots (`store/db/bots`)
Key: `Bot UUID`
```ts
{
  enabled : boolean,
  ml_boost: boolean,
  exchange: string,
  market:   string,
  strategy: string,
  state:    UserMetrics,
  candles:  string,
  features: string[],
  user:     string, // Username
  stop_time:	number, // UTC timestamp
  start_time:	number  // UTC timestamp 
}
```

## Authentication (`store/db/auth`)
Key: `Session UUID`
```ts
string // Username
```

