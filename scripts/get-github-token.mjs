import https from 'https';

const clientId = process.argv[2];

if (!clientId) {
    console.error("Usage: node get-github-token.mjs <YOUR_GITHUB_APP_CLIENT_ID>");
    console.error("You must create a GitHub App first with 'User permissions: Plan (Read-only)'.");
    process.exit(1);
}

function postJSON(url, data) {
    return new Promise((resolve, reject) => {
        const req = https.request(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Divoom-Widget/1.0'
            }
        }, (res) => {
            let body = '';
            res.on('data', d => body += d);
            res.on('end', () => {
                try { resolve(JSON.parse(body)); }
                catch (e) { resolve({ error: 'parse_error', body }); }
            });
        });
        req.on('error', reject);
        req.write(JSON.stringify(data));
        req.end();
    });
}

async function main() {
    console.log("Requesting device code from GitHub...");
    // 1. Request device and user code
    const initRes = await postJSON('https://github.com/login/device/code', {
        client_id: clientId,
        // The billing endpoint needs a user access token (OAuth).
        // For GitHub Apps, device flow scopes are implicit to the App's permissions.
    });

    if (initRes.error) {
        console.error("Authentication Error:", initRes);
        return;
    }

    console.log("\n=============================================");
    console.log(`1. Open this URL in your browser: ${initRes.verification_uri}`);
    console.log(`2. Enter the code: ${initRes.user_code}`);
    console.log("=============================================\n");
    console.log("Waiting for you to authorize... (this checks every 5 seconds)");

    // 2. Poll for token
    const pollInterval = (initRes.interval || 5) * 1000;
    
    while (true) {
        await new Promise(r => setTimeout(r, pollInterval));
        
        const tokenRes = await postJSON('https://github.com/login/oauth/access_token', {
            client_id: clientId,
            device_code: initRes.device_code,
            grant_type: 'urn:ietf:params:oauth:grant-type:device_code'
        });

        if (tokenRes.access_token) {
            console.log("\n✅ SUCCESS! Here is your token (starts with gho_):");
            console.log("\n" + tokenRes.access_token + "\n");
            console.log("Copy this into your .env file as GITHUB_OAUTH_TOKEN.");
            break;
        } else if (tokenRes.error === 'authorization_pending') {
            process.stdout.write(".");
        } else if (tokenRes.error === 'slow_down') {
            await new Promise(r => setTimeout(r, pollInterval));
        } else {
            console.error("\n❌ Error retrieving token:", tokenRes.error, tokenRes.error_description);
            break;
        }
    }
}

main().catch(console.error);