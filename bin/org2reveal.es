#!/usr/bin/env -S emacs --script
(setq debug-on-error t)
; see https://www.emacswiki.org/emacs/LoadPath
(let ((default-directory  "~/.emacs.d/elpa/develop"))
  (normal-top-level-add-subdirs-to-load-path))

(require 'ox-re-reveal)
(setq user-full-name "yuanqi.xhf")
(setq user-mail-address "yuanqi.xhf@alipay.com")
(setq org-re-reveal-root "/deps/reveal-js")
(setq org-re-reveal-title-slide "<h1>%t</h1>
<h2>%a</h2>
<h2>%e</h2>")
(let ((org-document-content "")
      this-read)
  (while (setq this-read (ignore-errors
                           (read-from-minibuffer "")))
    (setq org-document-content (concat org-document-content this-read "\n")))
  (with-temp-buffer
    (org-mode)
    (setq org-html-link-org-files-as-html nil)
    (setq link-org-files-as-md nil)
    (setq org-export-with-toc 1)
    (setq org-export-headline-levels 3)
    (setq org-export-with-sub-superscripts '{})
    (setq org-re-reveal-hlevel 1)
    (insert org-document-content)
    (org-export-to-buffer 're-reveal "*temp-reveal-buffer*")
    ;; (while (search-forward "file://" nil t)
    ;;        (replace-match "" nil t))
    (princ (buffer-string))))

