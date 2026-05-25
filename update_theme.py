import re
import os

css_additions = '''
        :root {
            --app-bg: rgba(255, 255, 255, 0.4);
            --app-border: rgba(0, 0, 0, 0.1);
            --text-main: #1f2937;
            --text-muted: #6b7280;
            --glass-bg: rgba(255, 255, 255, 0.6);
            --glass-border: rgba(255, 255, 255, 0.5);
            --card-inner: rgba(0, 0, 0, 0.05);
            --input-bg: rgba(255, 255, 255, 0.7);
            --input-border: rgba(0, 0, 0, 0.1);
            --accent-glow: rgba(16, 185, 129, 0.4);
            --btn-gradient: linear-gradient(-45deg, #059669, #10b981, #34d399, #6ee7b7);
            --vibrant-gradient: linear-gradient(135deg, #10b981 0%, #34d399 100%);
            --pill-bg: rgba(0, 0, 0, 0.05);
            --pill-glow: linear-gradient(90deg, #10b981 0%, #34d399 50%, #10b981 100%);
            --border-glow: linear-gradient(90deg, #34d399 0%, #10b981 50%, #34d399 100%);
            --status-text: #059669;
            --queue-grad-1: rgba(16, 185, 129, 0.15);
            --queue-grad-2: rgba(52, 211, 153, 0.15);
            --done-btn-bg: rgba(16, 185, 129, 0.15);
            --done-btn-hover: rgba(16, 185, 129, 0.25);
            --done-btn-text: #059669;
            --blur-decor-1: rgba(255, 255, 255, 0.8);
            --blur-decor-2: rgba(16, 185, 129, 0.3);
        }
        body.theme-dark {
            --app-bg: rgba(15, 23, 42, 0.4);
            --app-border: rgba(255, 255, 255, 0.1);
            --text-main: #ffffff;
            --text-muted: rgba(255, 255, 255, 0.6);
            --glass-bg: rgba(15, 23, 42, 0.7);
            --glass-border: rgba(255, 255, 255, 0.05);
            --card-inner: rgba(255, 255, 255, 0.05);
            --input-bg: rgba(0, 0, 0, 0.2);
            --input-border: rgba(255, 255, 255, 0.1);
            --accent-glow: rgba(37, 99, 235, 0.5);
            --btn-gradient: linear-gradient(-45deg, #1e3a8a, #1d4ed8, #3b82f6, #60a5fa);
            --vibrant-gradient: linear-gradient(135deg, #1d4ed8 0%, #3b82f6 100%);
            --pill-bg: #0f172a;
            --pill-glow: linear-gradient(90deg, #1d4ed8 0%, #3b82f6 50%, #1d4ed8 100%);
            --border-glow: linear-gradient(90deg, #60a5fa 0%, #3b82f6 50%, #60a5fa 100%);
            --status-text: #60a5fa;
            --queue-grad-1: rgba(30, 58, 138, 0.4);
            --queue-grad-2: rgba(59, 130, 246, 0.4);
            --done-btn-bg: rgba(59, 130, 246, 0.15);
            --done-btn-hover: rgba(59, 130, 246, 0.25);
            --done-btn-text: #60a5fa;
            --blur-decor-1: rgba(255, 255, 255, 0.05);
            --blur-decor-2: rgba(59, 130, 246, 0.2);
        }
'''

css_class_updates = '''
        .app-container {
            background: var(--app-bg);
            backdrop-filter: blur(40px);
            -webkit-backdrop-filter: blur(40px);
            border: 1px solid var(--app-border);
            border-radius: 40px;
            box-shadow: 0 30px 60px -12px rgba(0, 0, 0, 0.3);
            width: 100%;
            max-width: 72rem; /* Desktop width */
            padding: 2rem 2.5rem;
            color: var(--text-main);
            position: relative;
            overflow: hidden;
            transition: all 0.3s ease;
        }
        .glass-card {
            background-color: var(--glass-bg);
            border: 1px solid var(--glass-border);
            border-radius: 24px;
            transition: all 0.3s ease;
        }
        .vibrant-gradient {
            background: var(--vibrant-gradient);
            color: #ffffff;
        }
        .input-field {
            background-color: var(--input-bg);
            border: 1px solid var(--input-border);
            color: var(--text-main);
            transition: all 0.3s ease;
        }
        .input-field::placeholder {
            color: var(--text-muted);
        }
        .input-field:focus {
            background-color: var(--card-inner);
            border-color: var(--status-text);
            outline: none;
        }
        /* Custom scrollbar for webkit */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--status-text); border-radius: 3px; }

        /* Hide number spinners */
        input[type="number"]::-webkit-inner-spin-button,
        input[type="number"]::-webkit-outer-spin-button {
            -webkit-appearance: none;
            margin: 0;
        }
        input[type="number"] {
            -moz-appearance: textfield;
        }

        /* Custom Animations */
        @keyframes shimmerLine {
            0% { background-position: 200% center; }
            100% { background-position: 0% center; }
        }

        .animated-glow-bg {
            background: var(--pill-glow);
            background-size: 200% auto;
            animation: shimmerLine 2.5s linear infinite;
            box-shadow: 0 0 12px var(--accent-glow);
        }

        .animated-border-glow {
            background: var(--border-glow);
            background-size: 200% auto;
            animation: shimmerLine 2s linear infinite;
            box-shadow: 0 0 10px var(--accent-glow);
        }

        .btn-gradient {
            background: var(--btn-gradient);
            background-size: 300% auto;
            transition: all 0.4s ease-in-out;
            box-shadow: 0 4px 20px var(--accent-glow);
            color: #ffffff;
        }
        .btn-gradient:hover {
            background-position: 100% center;
            box-shadow: 0 12px 35px var(--accent-glow);
        }
        .btn-gradient:active {
            transform: scale(0.96);
            box-shadow: 0 2px 15px var(--accent-glow);
            filter: brightness(1.2);
        }
        .queue-card-bg {
            background: linear-gradient(135deg, var(--queue-grad-1), var(--queue-grad-2), var(--queue-grad-1));
            background-size: 200% 200%;
            animation: gradientShift 6s ease infinite;
        }
        .text-muted { color: var(--text-muted); }
        .text-main { color: var(--text-main); }
        .text-status { color: var(--status-text); }
        .border-app { border-color: var(--app-border); }
        .bg-inner { background-color: var(--card-inner); }
        .hover-bg-inner:hover { background-color: var(--input-bg); }
        .bg-pill { background-color: var(--pill-bg); }
        .blur-decor-1 { background-color: var(--blur-decor-1); }
        .blur-decor-2 { background-color: var(--blur-decor-2); }
        .done-btn {
            background-color: var(--done-btn-bg);
            color: var(--done-btn-text);
            transition: all 0.3s ease;
        }
        .done-btn:hover {
            background-color: var(--done-btn-hover);
        }
'''

def process_file(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. Update <style> block
    # Insert variables after <style>
    if '--app-bg:' not in content:
        content = re.sub(r'(<style>\s*)', r'\1' + css_additions, content)
    
    # Replace the old hardcoded CSS classes with the new ones
    start_str = '.app-container {'
    end_str = '</style>'
    start_idx = content.find(start_str)
    end_idx = content.find(end_str)
    
    if start_idx != -1 and end_idx != -1:
        content = content[:start_idx] + css_class_updates.strip() + '\n    ' + content[end_idx:]

    # 2. Update Tailwind classes
    replacements = {
        'text-white/40': 'text-muted',
        'text-white/50': 'text-muted',
        'text-white/60': 'text-muted',
        'text-white': 'text-main',
        'peer-checked:text-white': 'peer-checked:text-main',
        'hover:text-white': 'hover:text-main',
        'text-emerald-300': 'text-status',
        'text-emerald-400': 'text-status',
        'text-[#f72585]': 'text-status',
        'bg-[#1a1147]': 'bg-pill',
        'hover:bg-white/10': 'hover-bg-inner',
        'hover:bg-white/20': 'hover-bg-inner',
        'bg-white/10': 'bg-inner',
        'border-white/10': 'border-app',
        'border-white/5': 'border-app',
        'border-white/30': 'border-app',
        'border-white/20': 'border-app',
        'bg-white': 'blur-decor-1',
        'bg-pink-300': 'blur-decor-2',
        'bg-gradient-to-br from-[#4361ee]/40 via-[#f72585]/40 to-[#4361ee]/40 animate-bg-shift': 'queue-card-bg',
        'bg-[#f72585]/20 hover:bg-[#f72585]/40 text-[#f72585]': 'done-btn',
        'text-emerald-500': 'text-status',
        'border-white/15': 'border-app',
        'placeholder:text-white/40': '', # Removing placeholder color utility since we use ::placeholder
    }

    # Only replace in the body section
    body_start = content.find('<body>')
    body_end = content.find('</body>')
    
    if body_start != -1 and body_end != -1:
        body_content = content[body_start:body_end]
        for old, new in replacements.items():
            # word boundary replacement for tailwind classes to avoid partial matches
            body_content = re.sub(r'(?<=\s|\")' + re.escape(old) + r'(?=\s|\")', new, body_content)
        content = content[:body_start] + body_content + content[body_end:]

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

process_file('c:\\\\Users\\\\sclab\\\\Desktop\\\\CCProj\\\\static\\\\index.html')
process_file('c:\\\\Users\\\\sclab\\\\Desktop\\\\CCProj\\\\static\\\\admin.html')
print('Updated index.html and admin.html successfully')
