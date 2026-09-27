import {defineConfig} from "vite";
import react from "@vitejs/plugin-react";
import {fileURLToPath} from "node:url";
export default defineConfig({plugins:[react()],resolve:{alias:{"@":fileURLToPath(new URL(".",import.meta.url))}},build:{outDir:"../web",emptyOutDir:true},server:{proxy:{"/api":"http://127.0.0.1:8000"}}});
