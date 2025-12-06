# Railway CORS Configuration

## Problem
The frontend deployed on Vercel cannot access the backend on Railway due to CORS (Cross-Origin Resource Sharing) restrictions.

## Solution
Add your Vercel domain(s) to the `ALLOWED_ORIGINS` environment variable in Railway.

## Steps

### 1. Get Your Vercel Domain(s)
You need to add all Vercel domains that will access your backend:
- **Production domain**: `https://your-app.vercel.app` (if you have a custom domain)
- **Preview deployments**: `https://your-app-git-main-username.vercel.app` (for each branch)
- **Main branch**: `https://your-app-git-main-username.vercel.app`

From your error, your Vercel domain is:
```
https://malishaedu-agent-frontend-git-main-asifs-projects-70b80d55.vercel.app
```

### 2. Update Railway Environment Variable

1. Go to your Railway project dashboard
2. Click on your backend service
3. Go to **Variables** tab
4. Find or add the `ALLOWED_ORIGINS` variable
5. Set the value to include:
   - Your local development URL: `http://localhost:3000`
   - Your Vercel production domain(s)

**Example value:**
```
http://localhost:3000,https://malishaedu-agent-frontend-git-main-asifs-projects-70b80d55.vercel.app,https://malishaedu-agent-frontend.vercel.app
```

**Format:**
- Comma-separated list
- Include protocol (`http://` or `https://`)
- No trailing slashes
- Each domain on a new line or separated by commas

### 3. Redeploy
After updating the environment variable, Railway will automatically redeploy your service.

### 4. Verify
Check the Railway logs to see:
```
CORS allowed origins: ['http://localhost:3000', 'https://malishaedu-agent-frontend-git-main-asifs-projects-70b80d55.vercel.app', ...]
```

## Multiple Vercel Domains

If you have multiple Vercel deployments (production, preview, branches), add all of them:

```
http://localhost:3000,https://malishaedu-agent-frontend.vercel.app,https://malishaedu-agent-frontend-git-main-asifs-projects-70b80d55.vercel.app,https://malishaedu-agent-frontend-git-preview-asifs-projects-70b80d55.vercel.app
```

## Custom Domain

If you have a custom domain for your Vercel app, add it too:

```
http://localhost:3000,https://yourdomain.com,https://www.yourdomain.com
```

## Troubleshooting

### Still getting CORS errors?
1. Check Railway logs to see what origins are being allowed
2. Verify the exact domain in the browser console error
3. Make sure there are no typos in the domain
4. Ensure the protocol matches (`https://` not `http://`)
5. Wait for Railway to finish redeploying after changing the variable

### Testing Locally
For local development, make sure `ALLOWED_ORIGINS` includes:
```
http://localhost:3000
```

