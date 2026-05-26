# container-vpc-demo

Demo showing how a **Cloudflare Container** can call a **private API** that is
not reachable from the public internet — by intercepting outbound HTTP from
the container and routing it through a **Workers VPC** binding into a
**Cloudflare Tunnel**.

```
[Container] --curl http://acme-products.internal/products-->
  [MyContainer.outboundByHost handler in Worker runtime]
    --env.PRIVATE_API.fetch()-->
      [Workers VPC Service: acme-products]
        --cloudflared tunnel-->
          [Private API on 127.0.0.1:8080 on the origin VM]
```

## The three moving pieces

### 1. Workers VPC Service

A `vpc service` registered against a cloudflared tunnel. The service ID is
referenced from the Worker. Docs:
[Workers VPC overview](https://developers.cloudflare.com/workers-vpc/) ·
[Create a VPC Service](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/) ·
[Tunnel for VPC](https://developers.cloudflare.com/workers-vpc/configuration/tunnel/).

```bash
npx wrangler vpc service create acme-products \
  --type http \
  --tunnel-id <YOUR_TUNNEL_ID> \
  --hostname 127.0.0.1 \
  --http-port 8080
```

### 2. Worker with VPC Service binding

Configured in `wrangler.jsonc`:

```jsonc
"vpc_services": [
  {
    "binding": "PRIVATE_API",
    "service_id": "019dbc57-f657-7023-9b23-71c2646ecffa",
    "remote": true
  }
]
```

Docs:
[VPC Service binding](https://developers.cloudflare.com/workers-vpc/api/) ·
[Wrangler `vpc_services` config](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/#workers-binding-configuration).

### 3. Container with `outboundByHost` interception

In `src/index.ts`:

```ts
export class MyContainer extends Container<Env> {
  defaultPort = 8080;
}

MyContainer.outboundByHost = {
  "acme-products.internal": (req, env) => env.PRIVATE_API.fetch(req),
};
```

Inside the container, application code is unchanged HTTP:

```py
urllib.request.urlopen("http://acme-products.internal/products")
```

Docs:
[Container outbound traffic](https://developers.cloudflare.com/containers/platform-details/outbound-traffic/) ·
[Container → Workers bindings](https://developers.cloudflare.com/containers/platform-details/workers-connections/) ·
[Container class](https://developers.cloudflare.com/containers/container-class/).

## Why this is useful

- **No SDK or client library inside the container.** Any HTTP client (`curl`,
  `urllib`, `axios`, your language's stdlib) just works against a virtual
  hostname.
- **The outbound handler runs in the Workers runtime**, not in the container
  sandbox. It has full access to `env` — VPC bindings, secrets, KV, R2, D1,
  Durable Objects, etc. Credentials never enter the container.
- **No inbound firewall holes, no public IP** on the origin. The tunnel is
  outbound-only from the private network.

## Deploy

```bash
npm install
npx wrangler deploy
```

## Test

```bash
curl https://container-vpc-demo.<your-subdomain>.workers.dev/products
curl https://container-vpc-demo.<your-subdomain>.workers.dev/products/sku-001
curl https://container-vpc-demo.<your-subdomain>.workers.dev/health
```

## Measured timeouts

Probe with `scripts/probe-vpc-timeout.py` (parallel, streams one line per probe).

| Architecture | Worker route | Hard ceiling |
|---|---|---|
| Worker → VPC binding → tunnel → origin | `/direct-vpc-slow`, `/direct-mac-vpc-slow` | **~270 s** |
| Worker → Container → VPC binding → tunnel → origin | `/vpc-slow` | **~270 s** (same wall) |
| Worker → Container → internal sleep (no upstream) | `/sleep` | **~320 s** |
| Worker → public HTTPS → Cloudflare edge → tunnel → origin (with `proxy_read_timeout` cache rule + Access service token) | `/public-slow` | **`proxy_read_timeout` value** (we verified 600 s with the rule set to 10 min) |

The VPC binding's 270 s is independent of where the tunnel runs (verified by deploying a second tunnel on a Mac and re-probing — same exact 270.35 s wall, `Error: Network connection lost.`).

## Security: Access service token on the public hostname

`slow.demoflair.com` is protected by a Cloudflare Access self-hosted application with a Service-Auth policy referencing a service token. The Worker injects `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers from Worker secrets on every outbound fetch:

```ts
const resp = await fetch(target, {
  headers: {
    "CF-Access-Client-Id":     env.ACCESS_CLIENT_ID,
    "CF-Access-Client-Secret": env.ACCESS_CLIENT_SECRET,
  },
});
```

- Direct unauthenticated curl to `slow.demoflair.com` → **HTTP 403**.
- Worker `/public-slow` route → HTTP 200 (Access allows).
- Credentials never leave the Worker runtime; container code never sees them.
