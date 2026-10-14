#!/usr/bin/env -S emacs --script
(setq debug-on-error t)
(dolist (dir (split-string (or (getenv "W_ORG_LOAD_PATH") "") path-separator t))
  (add-to-list 'load-path (expand-file-name dir))
  (let ((default-directory (file-name-as-directory (expand-file-name dir))))
    (when (file-directory-p default-directory)
      (normal-top-level-add-subdirs-to-load-path))))

(require 'ox-re-reveal)
(setq user-full-name (or (getenv "W_ORG_AUTHOR") ""))
(setq user-mail-address (or (getenv "W_ORG_EMAIL") ""))
(setq org-re-reveal-root (or (getenv "W_REVEAL_ROOT") "/reveal-js"))
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

